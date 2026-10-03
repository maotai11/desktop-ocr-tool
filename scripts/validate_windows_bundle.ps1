# No Python/pip, network, downloads, elevation or firewall changes are needed.
# This records a candidate gate; a clean-machine and GUI workflow audit is separate.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundleDirectory,
    [Parameter(Mandatory = $true)][string]$ReportDirectory,
    [ValidateRange(5, 600)][int]$TimeoutSeconds = 180
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$summary = [ordered]@{
    schema = 1; passed = $false; clean_machine_verified = $false
    network_observation = 'NOT_RUN'; mixed_dpi_gate = 'NOT_RUN'; probes = @{}
}
try {
    $bundle = (Resolve-Path -LiteralPath $BundleDirectory).Path
    $reports = New-Item -ItemType Directory -Path $ReportDirectory -Force
    $summary.host = [ordered]@{
        os = (Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture)
        cpu = (Get-CimInstance Win32_Processor | Select-Object Name, AddressWidth)
        python_on_path = [bool](Get-Command python, python3, pip -ErrorAction SilentlyContinue)
        # PATH inventory cannot establish absence of installations or OCR caches.
        installation_and_cache_audit = 'NOT_RUN'
    }
    $manifestPath = Join-Path $bundle 'BUILD_MANIFEST.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.schema -ne 2 -or $manifest.platform -ne 'win32') { throw 'Invalid Windows build manifest' }
    if ($manifest.executable_name -notmatch '^DesktopOCRTool-v[0-9A-Za-z.+_-]+\.exe$') { throw 'Unsafe executable name' }
    $exe = Join-Path $bundle $manifest.executable_name
    $hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -cne $manifest.executable_sha256) { throw 'Executable SHA256 differs from manifest' }
    $sourceHash = (Get-FileHash -LiteralPath (Join-Path $bundle 'SOURCE_MANIFEST.json') -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sourceHash -cne $manifest.source_manifest_sha256) { throw 'Source manifest SHA256 mismatch' }
    $summary.executable_sha256 = $hash
    $summary.version = $manifest.version
    foreach ($probe in @(
        @{ mode = '--self-test'; name = 'frozen-selftest.json'; kind = 'ocr_database' },
        @{ mode = '--smoke-app'; name = 'frozen-app-smoke.json'; kind = 'application_lifecycle' }
    )) {
        $report = Join-Path $reports.FullName $probe.name
        # Never accept a report left over from a different launched process.
        if (Test-Path -LiteralPath $report) { Remove-Item -LiteralPath $report }
        $process = Start-Process -FilePath $exe -ArgumentList @($probe.mode, "`"$report`"") -PassThru
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            & taskkill.exe /PID $process.Id /T /F | Out-Null
            throw "$($probe.mode) exceeded ${TimeoutSeconds}s; launched tree terminated"
        }
        $process.Refresh()
        if ($process.ExitCode -ne 0) { throw "$($probe.mode) failed with exit code $($process.ExitCode)" }
        $result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
        if ($result.schema -ne 2 -or $result.probe -cne $probe.kind -or
            $result.passed -ne $true -or $result.frozen -ne $true -or
            $result.platform -cne 'win32' -or $result.qt_platform -cne 'windows' -or
            $result.version -cne $manifest.version -or $result.executable_sha256 -cne $hash -or
            $result.clean_machine_verified -ne $false -or $result.database_integrity -cne 'ok') {
            throw "Incomplete or mismatched report: $($probe.name)"
        }
        if ($probe.mode -eq '--self-test') {
            if ($result.telemetry_env -cne '1') { throw 'Telemetry opt-out hook missing' }
            foreach ($key in @('qt', 'models', 'ocr', 'database')) {
                if ($result.checks.$key -ne $true) { throw "Failed self-test check: $key" }
            }
            foreach ($key in @('det', 'rec', 'cls')) {
                if ($result.models.$key.sha256 -cne $manifest.models.$key.sha256) { throw "Model hash mismatch: $key" }
            }
        } else {
            if ($result.phase_a -ne $true -or $result.engine_ready -ne $true -or $result.shutdown_clean -ne $true) {
                throw 'Application lifecycle incomplete'
            }
            foreach ($key in @('capture', 'ocr', 'database', 'hotkeys')) {
                if ($result.threads_stopped.$key -ne $true) { throw "Thread still running: $key" }
            }
        }
        $summary.probes[$probe.name] = @{ status = 'PASSED'; sha256 = (Get-FileHash -LiteralPath $report -Algorithm SHA256).Hash.ToLowerInvariant() }
    }
    if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() -cne $hash) { throw 'Executable changed during validation' }
    $summary.passed = $true
} catch {
    $summary.error = $_.Exception.Message
    Write-Warning $summary.error
} finally {
    if (Test-Path -LiteralPath $ReportDirectory -PathType Container) {
        $summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $ReportDirectory 'candidate-gate.json') -Encoding UTF8
    }
}
if (-not $summary.passed) { exit 1 }
exit 0
