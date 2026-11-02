# Build-runner portability check: one EXE, empty profile/cache, system-only PATH.
# This is not a certification of an independently provisioned clean Windows host.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundleDirectory,
    [Parameter(Mandatory = $true)][string]$ReportDirectory,
    [ValidateRange(15, 600)][int]$TimeoutSeconds = 240
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$scratch = $null
$ruleName = 'InkPick-portable-' + [guid]::NewGuid().ToString()
$ruleCreated = $false
$activeProcess = $null
$summary = [ordered]@{
    schema = 1; probe = 'isolated_profile'; passed = $false
    clean_machine_verified = $false; profile_empty = $false
    copied_executable_only = $false; path_isolated = $false
    python_env_cleared = $false; qt_env_cleared = $false
    outer_network_blocked = $false; probes = @{}
}
try {
    $reports = (New-Item -ItemType Directory -Path $ReportDirectory -Force).FullName
    $bundle = (Resolve-Path -LiteralPath $BundleDirectory).Path
    $manifest = Get-Content -LiteralPath (Join-Path $bundle 'BUILD_MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schema -ne 2 -or $manifest.platform -cne 'win32' -or
        $manifest.version -cne '1.8.0-rc.3' -or
        $manifest.executable_name -cne 'InkPick-v1.8.0-rc.3.exe') {
        throw 'Unexpected portable candidate identity'
    }
    $original = Join-Path $bundle $manifest.executable_name
    $hash = (Get-FileHash -LiteralPath $original -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -cne $manifest.executable_sha256) { throw 'Portable candidate SHA256 mismatch' }
    $summary.executable_sha256 = $hash
    $summary.version = $manifest.version

    # The generated path exercises Unicode and spaces without using user files.
    $scratch = Join-Path ([IO.Path]::GetTempPath()) ('InkPick 離線測試 ' + [guid]::NewGuid().ToString())
    $program = Join-Path $scratch '程式'
    $profile = Join-Path $scratch '全新使用者'
    $roaming = Join-Path $profile 'AppData\Roaming'
    $local = Join-Path $profile 'AppData\Local'
    $temp = Join-Path $local 'Temp'
    $dataRoot = Join-Path $scratch '應用資料'
    foreach ($path in @($program, $profile, $roaming, $local, $temp, $dataRoot)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
    $summary.profile_empty = (@(Get-ChildItem -LiteralPath $temp -Force).Count -eq 0 -and
        @(Get-ChildItem -LiteralPath $dataRoot -Force).Count -eq 0)
    $exe = Join-Path $program $manifest.executable_name
    Copy-Item -LiteralPath $original -Destination $exe
    if (@(Get-ChildItem -LiteralPath $program -File -Force).Count -ne 1 -or
        (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() -cne $hash) {
        throw 'Portable program directory is not the single verified EXE'
    }
    $summary.copied_executable_only = $true
    $systemRoot = [Environment]::GetEnvironmentVariable('SystemRoot')
    if ([string]::IsNullOrWhiteSpace($systemRoot) -or -not (Test-Path -LiteralPath (Join-Path $systemRoot 'System32'))) {
        throw 'Windows system directory unavailable'
    }
    $service = Get-Service -Name MpsSvc
    if ($service.Status -ne 'Running' -or @(Get-NetFirewallProfile | Where-Object { -not $_.Enabled }).Count -gt 0) {
        throw 'Windows firewall is not enabled'
    }
    New-NetFirewallRule -Name $ruleName -DisplayName $ruleName -Direction Outbound -Program $exe -Action Block -Profile Any | Out-Null
    $ruleCreated = $true
    $rule = Get-NetFirewallRule -Name $ruleName
    $application = $rule | Get-NetFirewallApplicationFilter
    if ($rule.Enabled -ne 'True' -or $rule.Direction -ne 'Outbound' -or $rule.Action -ne 'Block' -or
        -not [string]::Equals($application.Program, $exe, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Portable EXE firewall rule was not applied'
    }
    $summary.outer_network_blocked = $true

    foreach ($probe in @(
        @{ mode = '--self-test'; name = 'isolated-selftest.json'; kind = 'ocr_database' },
        @{ mode = '--smoke-app'; name = 'isolated-app-smoke.json'; kind = 'application_lifecycle' },
        @{ mode = '--self-test-handwriting'; name = 'isolated-handwriting.json'; kind = 'handwriting_lifecycle' },
        @{ mode = '--self-test-handwriting-image'; name = 'isolated-handwriting-image.json'; kind = 'handwriting_image_lifecycle' },
        @{ mode = '--self-test-documents'; name = 'isolated-documents.json'; kind = 'document_template_lifecycle' }
    )) {
        $report = Join-Path $reports $probe.name
        if (Test-Path -LiteralPath $report) { Remove-Item -LiteralPath $report }
        $start = [Diagnostics.ProcessStartInfo]::new()
        $start.FileName = $exe
        $start.WorkingDirectory = $program
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.ArgumentList.Add($probe.mode)
        $start.ArgumentList.Add($report)
        # Start from no inherited environment: no Python, Qt, credentials or
        # runner caches can be reached through a child's environment selectors.
        $start.Environment.Clear()
        $environmentValues = @{
            SystemRoot = $systemRoot; WINDIR = $systemRoot
            SystemDrive = [IO.Path]::GetPathRoot($systemRoot).TrimEnd('\')
            COMSPEC = Join-Path $systemRoot 'System32\cmd.exe'
            PATH = ((Join-Path $systemRoot 'System32') + ';' + $systemRoot)
            PATHEXT = '.COM;.EXE;.BAT;.CMD'
            USERPROFILE = $profile; HOME = $profile
            APPDATA = $roaming; LOCALAPPDATA = $local; TEMP = $temp; TMP = $temp
            DESKTOP_OCR_HOME = $dataRoot; QT_QPA_PLATFORM = 'windows'
            ORT_DISABLE_TELEMETRY = '1'; OMP_NUM_THREADS = '2'; OPENBLAS_NUM_THREADS = '2'
        }
        foreach ($key in $environmentValues.Keys) { $start.Environment[$key] = $environmentValues[$key] }
        $summary.path_isolated = ($start.Environment['PATH'] -ceq ((Join-Path $systemRoot 'System32') + ';' + $systemRoot))
        $summary.python_env_cleared = (-not $start.Environment.ContainsKey('PYTHONHOME') -and -not $start.Environment.ContainsKey('PYTHONPATH'))
        $summary.qt_env_cleared = (-not $start.Environment.ContainsKey('QT_PLUGIN_PATH') -and -not $start.Environment.ContainsKey('QT_QPA_PLATFORM_PLUGIN_PATH'))
        $activeProcess = [Diagnostics.Process]::Start($start)
        if (-not $activeProcess.WaitForExit($TimeoutSeconds * 1000)) {
            $activeProcess.Kill($true)
            $activeProcess.WaitForExit(10000) | Out-Null
            throw 'Portable EXE probe exceeded its deadline'
        }
        if ($activeProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $report -PathType Leaf)) {
            throw 'Portable EXE probe failed or did not produce fresh evidence'
        }
        $activeProcess.Dispose()
        $activeProcess = $null
        $result = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($result.probe -cne $probe.kind -or $result.passed -ne $true -or
            $result.frozen -ne $true -or $result.platform -cne 'win32' -or
            $result.version -cne $manifest.version -or
            $result.executable_sha256 -cne $hash -or $result.clean_machine_verified -ne $false) {
            throw 'Portable EXE probe identity or result differs from candidate'
        }
        if ($probe.kind -ceq 'document_template_lifecycle') {
            # This probe uses real PDFium/Pillow without creating a Qt GUI.
            # Preserve its narrower claim and require every fixed lifecycle
            # check, rather than accepting the top-level passed flag alone.
            $checkNames = @('pdf_rendering', 'image_rendering', 'region_inheritance',
                'page_override', 'template_merge', 'source_unchanged', 'temporary_files_cleaned')
            if ($result.schema -isnot [int64] -and $result.schema -isnot [int]) {
                throw 'Document probe schema has the wrong type'
            }
            if ($result.schema -ne 1 -or $result.fixture_kind -cne 'SYNTHETIC_SOLID_RECTANGLES' -or
                $result.passed -isnot [bool] -or $result.frozen -isnot [bool] -or
                $result.clean_machine_verified -isnot [bool] -or
                $result.ocr_accuracy_tested -isnot [bool] -or $result.ocr_accuracy_tested -ne $false -or
                $result.model_inference_tested -isnot [bool] -or $result.model_inference_tested -ne $false -or
                $result.checks -isnot [System.Management.Automation.PSCustomObject]) {
                throw 'Incomplete synthetic document probe contract'
            }
            $keys = @($result.checks.PSObject.Properties.Name | Sort-Object)
            if (($keys -join '|') -cne (($checkNames | Sort-Object) -join '|')) {
                throw 'Incomplete document lifecycle checks'
            }
            foreach ($name in $checkNames) {
                if ($result.checks.$name -isnot [bool] -or $result.checks.$name -ne $true) {
                    throw 'Document lifecycle check failed'
                }
            }
            foreach ($name in @('error', 'error_code', 'traceback')) {
                if ($result.PSObject.Properties.Name -ccontains $name) {
                    throw 'Document probe includes a failure diagnostic'
                }
            }
        } elseif ($result.qt_platform -cne 'windows') {
            throw 'Portable Qt probe did not use the Windows platform'
        }
        $summary.probes[$probe.name] = @{
            status = 'PASSED'
            sha256 = (Get-FileHash -LiteralPath $report -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() -cne $hash -or
        -not $summary.profile_empty -or -not $summary.path_isolated -or
        -not $summary.python_env_cleared -or -not $summary.qt_env_cleared) {
        throw 'Portable isolation evidence is incomplete'
    }
    $summary.passed = $true
} catch {
    $summary.error_code = 'PORTABLE_PROFILE_CHECK_FAILED'
    Write-Warning $_.Exception.Message
} finally {
    $cleanupFailed = $false
    if ($null -ne $activeProcess) {
        try {
            if (-not $activeProcess.HasExited) {
                $activeProcess.Kill($true)
                if (-not $activeProcess.WaitForExit(10000)) { throw 'Portable process did not stop' }
            }
        } catch { $cleanupFailed = $true }
        try { $activeProcess.Dispose() } catch { $cleanupFailed = $true }
    }
    # Every cleanup is attempted independently. Only the unique rule and the
    # generated directory owned by this invocation can be removed.
    try {
        if ($ruleCreated -or $null -ne (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)) {
            Remove-NetFirewallRule -Name $ruleName
        }
        if ($null -ne (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)) {
            throw 'Portable firewall rule remained after cleanup'
        }
    } catch { $cleanupFailed = $true }
    try {
        if ($null -ne $scratch -and (Test-Path -LiteralPath $scratch -PathType Container)) {
            Remove-Item -LiteralPath $scratch -Recurse -Force
            if (Test-Path -LiteralPath $scratch) { throw 'Portable temporary files remained' }
        }
    } catch { $cleanupFailed = $true }
    if ($cleanupFailed) {
        $summary.passed = $false
        $summary.error_code = 'PORTABLE_PROFILE_CLEANUP_FAILED'
    }
    # Report writing runs last even when process/rule/directory cleanup failed.
    try {
        if (-not (Test-Path -LiteralPath $ReportDirectory -PathType Container)) {
            throw 'Portable report directory unavailable'
        }
        $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $ReportDirectory 'isolated-profile-gate.json') -Encoding UTF8
    } catch {
        $summary.passed = $false
        Write-Warning 'PORTABLE_PROFILE_REPORT_WRITE_FAILED'
    }
}
if (-not $summary.passed) { exit 1 }
exit 0
