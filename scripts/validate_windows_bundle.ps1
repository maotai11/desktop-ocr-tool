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
# Both offline profiles must agree with the build manifest, including decoder identity.
$requiredProfiles = @('v6-small', 'v6-medium')
$defaultProfile = 'v6-small'
function Get-ObjectKeys($Value) {
    if ($Value -is [System.Collections.IDictionary]) { return @($Value.Keys) }
    if ($Value -is [System.Management.Automation.PSCustomObject]) { return @($Value.PSObject.Properties.Name) }
    throw 'Expected a JSON object'
}
function Assert-ObjectKeys($Value, [string[]]$Names, [string]$Context) {
    if ((@(Get-ObjectKeys $Value | Sort-Object) -join '|') -cne (@($Names | Sort-Object) -join '|')) {
        throw "Unexpected properties: $Context"
    }
}
function Assert-JsonEqual($Actual, $Expected, [string]$Context) {
    if ($Expected -is [System.Collections.IDictionary] -or $Expected -is [System.Management.Automation.PSCustomObject]) {
        $keys = @(Get-ObjectKeys $Expected)
        Assert-ObjectKeys $Actual $keys $Context
        foreach ($key in $keys) { Assert-JsonEqual $Actual.$key $Expected.$key "$Context/$key" }
    } elseif ($Expected -is [System.Array]) {
        if ($Actual -isnot [System.Array] -or $Actual.Count -ne $Expected.Count) { throw "Array mismatch: $Context" }
        for ($i = 0; $i -lt $Expected.Count; $i++) { Assert-JsonEqual $Actual[$i] $Expected[$i] "$Context/$i" }
    } elseif (($Actual | ConvertTo-Json -Compress -Depth 12) -cne ($Expected | ConvertTo-Json -Compress -Depth 12)) {
        throw "Value mismatch: $Context"
    }
}
function Assert-ModelsEqual($Actual, $Expected, [string]$Context) {
    Assert-ObjectKeys $Actual @('det', 'rec', 'cls') $Context
    Assert-ObjectKeys $Expected @('det', 'rec', 'cls') "$Context/expected"
    foreach ($role in @('det', 'rec', 'cls')) {
        $portable = [ordered]@{}
        foreach ($key in @(Get-ObjectKeys $Actual.$role)) {
            if ($key -cne 'absolute_path') { $portable[$key] = $Actual.$role.$key }
        }
        Assert-JsonEqual $portable $Expected.$role "$Context/$role"
    }
}
function Assert-ProfileManifests($Manifest) {
    $fixtures = $Manifest.ocr_validation_fixtures.cases
    if ($fixtures -isnot [System.Array] -or $fixtures.Count -ne 3 -or
        (@($fixtures.id | Sort-Object) -join '|') -cne 'amount|date_api|traditional') {
        throw 'Incomplete literal OCR fixture manifest'
    }
    foreach ($case in $fixtures) {
        if ($case.file -cnotmatch '^[^/\\:]+\.png$' -or $case.image_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            [string]::IsNullOrWhiteSpace($case.ground_truth)) { throw 'Invalid literal OCR fixture manifest' }
        Assert-ObjectKeys $case.expected $requiredProfiles 'fixture/expected'
        foreach ($profile in $requiredProfiles) {
            if ($case.expected.$profile -cne $case.ground_truth) { throw 'Fixture expectation differs from literal ground truth' }
        }
    }
    if (@($fixtures.file | Select-Object -Unique).Count -ne 3) { throw 'Duplicate literal OCR fixture path' }
    Assert-ObjectKeys $Manifest.model_profiles $requiredProfiles 'build/model_profiles'
    if ($Manifest.default_model_profile -cne $defaultProfile) { throw 'Unexpected default model profile' }
    Assert-ModelsEqual $Manifest.models $Manifest.model_profiles.$defaultProfile 'build/default_models'
    foreach ($profile in $requiredProfiles) {
        $models = $Manifest.model_profiles.$profile
        Assert-ObjectKeys $models @('det', 'rec', 'cls') "build/$profile"
        foreach ($role in @('det', 'rec', 'cls')) {
            $info = $models.$role
            if ($info.sha256 -cnotmatch '^[0-9a-f]{64}$' -or $info.size_bytes -le 0 -or
                [string]::IsNullOrWhiteSpace($info.version) -or
                $info.path -cnotmatch '^models/.+\.onnx$' -or
                $info.path -match '\\|(^|/)\.\.(/|$)|:') { throw "Invalid model asset: $profile/$role" }
        }
        $rec = $models.rec
        if ($rec.character_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            $rec.decoder_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            $rec.character_count -le 0 -or $rec.output_classes -ne ($rec.character_count + 2)) {
            throw "Invalid recognition metadata: $profile"
        }
    }
}
function Assert-ModelProfileProbes($Result, $Manifest) {
    Assert-ObjectKeys $Result.model_profiles $requiredProfiles 'selftest/model_profiles'
    Assert-ModelsEqual $Result.models $Manifest.models 'selftest/default_models'
    foreach ($profile in $requiredProfiles) {
        $probe = $Result.model_profiles.$profile
        $models = $Manifest.model_profiles.$profile
        if ($probe.passed -isnot [bool] -or $probe.passed -ne $true) { throw "Failed model-profile probe: $profile" }
        Assert-ModelsEqual $probe.models $models "selftest/$profile/models"
        $recognition = $probe.model_identity.recognition
        if ($recognition.input_shape -isnot [System.Array] -or $recognition.input_shape.Count -ne 4 -or
            $recognition.input_shape[1] -ne 3 -or
            $recognition.input_shape[2] -ne 48 -or
            $recognition.output_shape -isnot [System.Array] -or $recognition.output_shape.Count -ne 3 -or
            $recognition.output_shape[2] -ne $models.rec.output_classes) { throw "Tensor shape mismatch: $profile" }
        $expectedIdentity = [ordered]@{
            profile = $profile; runtime = 'rapidocr_onnxruntime'; runtime_version = '1.4.4'
            recognition_batch = 1; max_recognition_width = 4096
            recognition = [ordered]@{
                character_sha256 = $models.rec.character_sha256; character_count = $models.rec.character_count
                output_classes = $models.rec.output_classes; decoder_sha256 = $models.rec.decoder_sha256
                providers = @('CPUExecutionProvider'); recognition_shape = @(3, 48, 320)
                input_type = 'tensor(float)'; input_shape = $recognition.input_shape
                output_shape = $recognition.output_shape; blank_index = 0; space_index = ($models.rec.output_classes - 1)
            }
        }
        Assert-JsonEqual $probe.model_identity $expectedIdentity "selftest/$profile/model_identity"
        $modelVersion = $profile + ';' + ((@('cls', 'det', 'rec') | ForEach-Object {
            $_ + ':' + $models.$_.version + ':' + $models.$_.sha256
        }) -join ';')
        if (($probe.ocr_result.text -creplace '\s', '') -cne 'DesktopOCR12345' -or
            $probe.ocr_result.status -cnotin @('done', 'needs_review') -or
            $probe.ocr_result.engine -cne 'rapidocr_onnxruntime' -or
            $probe.ocr_result.model_version -cne $modelVersion) { throw "OCR/provenance mismatch: $profile" }
        if ($probe.fixtures -isnot [System.Array] -or $probe.fixtures.Count -ne 3 -or
            (@($probe.fixtures.id | Sort-Object) -join '|') -cne 'amount|date_api|traditional') {
            throw "Incomplete literal OCR fixture results: $profile"
        }
        foreach ($case in $Manifest.ocr_validation_fixtures.cases) {
            $observed = @($probe.fixtures | Where-Object { $_.id -ceq $case.id })[0]
            if ($observed.image_sha256 -cne $case.image_sha256 -or $observed.ground_truth -cne $case.ground_truth -or
                $observed.result.text -cne $case.ground_truth -or $observed.result.status -cnotin @('done', 'needs_review') -or
                $observed.result.engine -cne 'rapidocr_onnxruntime' -or $observed.result.model_version -cne $modelVersion) {
                throw "Literal OCR fixture/provenance mismatch: $profile/$($case.id)"
            }
        }
    }
    Assert-JsonEqual $Result.ocr_result $Result.model_profiles.$defaultProfile.ocr_result 'selftest/default_ocr'
}

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
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schema -ne 2 -or $manifest.platform -ne 'win32') { throw 'Invalid Windows build manifest' }
    Assert-ProfileManifests $manifest
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
        $result = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
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
            Assert-ModelProfileProbes $result $manifest
            $summary.model_profiles = @{}
            foreach ($profile in $requiredProfiles) { $summary.model_profiles[$profile] = 'PASSED' }
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
