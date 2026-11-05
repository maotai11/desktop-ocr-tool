# Build-only, local native-payload assessment. Never run by the application.
# https://learn.microsoft.com/en-us/defender-endpoint/command-line-arguments-microsoft-defender-antivirus
# -DisableRemediation ignores exclusions, scans archives and leaves detections
# unremediated (return code 2). Its detections are not recorded in the event log.
# -Timeout is measured in DAYS; the foreground process has a separate deadline.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Output,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{40}$')][string]$SourceCommit,
    [string]$Inventory,
    [ValidateRange(30, 900)][int]$TimeoutSeconds = 300,
    [string]$PythonExecutable = 'python'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Native payload helper functions: exercised with fixtures without invoking AV.
function ConvertTo-NativeArgument([string]$Value) {
    # Windows CommandLineToArgv/C runtime quoting; do not invoke cmd.exe.
    if ($Value.IndexOf([char]0) -ge 0) { throw 'NATIVE_UNSAFE_ARGUMENT' }
    return '"' + ([regex]::Replace($Value, '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}

function Write-NativeJson([string]$Path, $Value) {
    $text = $Value | ConvertTo-Json -Depth 40
    [IO.File]::WriteAllText($Path, $text, [Text.UTF8Encoding]::new($false))
}

function Get-NativeField($Object, [string]$Name) {
    if ($null -eq $Object -or $null -eq $Object.PSObject.Properties[$Name]) {
        throw 'NATIVE_MISSING_FIELD'
    }
    return $Object.$Name
}

function Resolve-NativeApplication([string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Name) -or $Name.IndexOf([char]0) -ge 0) {
        throw 'NATIVE_APPLICATION_UNAVAILABLE'
    }
    if ([IO.Path]::IsPathRooted($Name) -or $Name.Contains('/') -or $Name.Contains('\')) {
        # Explicit overrides are literal paths, including spaces and brackets.
        $source = (Get-Item -LiteralPath $Name -Force -ErrorAction Stop).FullName
    } else {
        # Get-Command can return several PATH matches. A .Source projection on
        # that collection creates an array, which is not an executable path.
        $commands = @(Get-Command -Name $Name -CommandType Application -ErrorAction Stop)
        if ($commands.Count -eq 0) { throw 'NATIVE_APPLICATION_UNAVAILABLE' }
        $source = $commands[0].Source
    }
    if ($source -isnot [string] -or [string]::IsNullOrWhiteSpace($source) -or
        -not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw 'NATIVE_APPLICATION_UNAVAILABLE'
    }
    return (Get-Item -LiteralPath $source -Force -ErrorAction Stop).FullName
}

function Assert-NativePlainPath([string]$Path, [string]$Root) {
    $full = [IO.Path]::GetFullPath($Path)
    $base = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    if (-not $full.StartsWith($base + [IO.Path]::DirectorySeparatorChar,
                             [StringComparison]::OrdinalIgnoreCase)) {
        throw 'NATIVE_PATH_ESCAPED'
    }
    $node = Get-Item -LiteralPath $full -Force
    while ($null -ne $node -and $node.FullName.Length -ge $base.Length) {
        if ($node.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'NATIVE_REDIRECTED_PATH'
        }
        $node = if ($node -is [IO.FileInfo]) { $node.Directory } else { $node.Parent }
    }
    return $full
}

function Assert-NativeScanScope([string]$Root, $RelativePaths) {
    if ($RelativePaths -isnot [Array] -or $RelativePaths.Count -lt 2 -or $RelativePaths.Count -gt 5000) {
        throw 'NATIVE_INCOMPLETE_SCAN_SCOPE'
    }
    $expected = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($relative in $RelativePaths) {
        if ($relative -isnot [string] -or $relative -notmatch '^(candidate|payload)/' -or
            $relative.Contains('\') -or $relative.Contains(':') -or
            @($relative.Split('/') | Where-Object { $_ -in @('', '.', '..') }).Count -ne 0) {
            throw 'NATIVE_UNSAFE_SCAN_PATH'
        }
        $full = Assert-NativePlainPath (Join-Path $Root $relative) $Root
        if (-not (Get-Item -LiteralPath $full -Force).PSIsContainer -and $expected.Add($full)) {
            continue
        }
        throw 'NATIVE_DUPLICATE_SCAN_PATH'
    }
    $actual = @(Get-ChildItem -LiteralPath $Root -Recurse -Force)
    if (@($actual | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count -ne 0) {
        throw 'NATIVE_REDIRECTED_PATH'
    }
    $files = @($actual | Where-Object { -not $_.PSIsContainer })
    if ($files.Count -ne $expected.Count -or
        @($files | Where-Object { -not $expected.Contains($_.FullName) }).Count -ne 0) {
        throw 'NATIVE_INCOMPLETE_SCAN_SCOPE'
    }
    return $expected.Count
}

function Assert-NativeDefenderReadiness($Status, $Preference, [DateTime]$Now) {
    foreach ($key in @('AMServiceEnabled', 'AntivirusEnabled')) {
        $value = Get-NativeField $Status $key
        if ($value -isnot [bool] -or -not $value) { throw 'NATIVE_DEFENDER_UNAVAILABLE' }
    }
    $engine = [string](Get-NativeField $Status 'AMEngineVersion')
    $signature = [string](Get-NativeField $Status 'AntivirusSignatureVersion')
    if ($engine -notmatch '^\d+(\.\d+){2,3}$' -or $engine -match '^0+(\.0+)+$' -or
        $signature -notmatch '^\d+(\.\d+){2,3}$' -or $signature -match '^0+(\.0+)+$') {
        throw 'NATIVE_DEFENDER_UNAVAILABLE'
    }
    try { $updated = ([DateTime](Get-NativeField $Status 'AntivirusSignatureLastUpdated')).ToUniversalTime() }
    catch { throw 'NATIVE_DEFENDER_SIGNATURE_DATE_INVALID' }
    $age = ($Now.ToUniversalTime() - $updated).TotalHours
    $samples = [int](Get-NativeField $Preference 'SubmitSamplesConsent')
    $maps = [int](Get-NativeField $Preference 'MAPSReporting')
    # Read existing policy only. Do not disable cloud protection or add exclusions.
    if ($samples -ne 2 -or $maps -ne 0) { throw 'NATIVE_DEFENDER_NOT_LOCAL_ONLY' }
    $realtime = Get-NativeField $Status 'RealTimeProtectionEnabled'
    $mode = [string](Get-NativeField $Status 'AMRunningMode')
    if ($realtime -isnot [bool] -or $mode -notmatch '^[A-Za-z][A-Za-z ._-]{0,63}$') {
        throw 'NATIVE_DEFENDER_STATUS_INVALID'
    }
    # A stale-date exception is refreshable only after the service, engine and
    # existing local-only policy passed. Future/invalid dates never refresh.
    if ($age -lt 0) { throw 'NATIVE_DEFENDER_SIGNATURE_DATE_INVALID' }
    if ($age -gt 72) { throw 'NATIVE_DEFENDER_SIGNATURE_STALE' }
    return [ordered]@{
        scanner = 'Microsoft Defender Antivirus'; scanner_version = $engine
        antivirus_signature_version = $signature; signature_updated_at = $updated.ToString('o')
        am_running_mode = $mode; real_time_protection_enabled = $realtime
        sample_submission_consent = $samples; maps_reporting = $maps
    }
}

function Get-NativeFreshDefenderReadiness(
        [Parameter(Mandatory = $true)][string]$LogPrefix,
        [ValidateRange(1, 180)][int]$MaxSeconds = 180) {
    try {
        return Assert-NativeDefenderReadiness (Get-MpComputerStatus) (Get-MpPreference) ([DateTime]::UtcNow)
    } catch {
        if ($_.Exception.Message -cne 'NATIVE_DEFENDER_SIGNATURE_STALE') { throw }
    }
    # Build-only definition download from Microsoft's official source. No
    # application/model update, preference change or sample submission occurs.
    $scanner = Get-NativeDefenderScanner
    $code = Invoke-NativeBoundedProcess $scanner @('-SignatureUpdate', '-MMPC') $LogPrefix $MaxSeconds
    if ($code -ne 0) { throw 'NATIVE_DEFENDER_SIGNATURE_UPDATE_FAILED' }
    # Do not infer freshness from the updater's exit code: reread the same
    # complete readiness policy, including the unchanged 72-hour requirement.
    return Assert-NativeDefenderReadiness (Get-MpComputerStatus) (Get-MpPreference) ([DateTime]::UtcNow)
}

function Get-NativeDefenderScanner {
    $candidates = @()
    $platform = [IO.Path]::GetFullPath((Join-Path $env:ProgramData 'Microsoft/Windows Defender/Platform'))
    if (Test-Path -LiteralPath $platform -PathType Container) {
        $candidates += @(Get-ChildItem -LiteralPath $platform -Directory -Force |
            Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+(-\d+)?$' } |
            Sort-Object -Property @{ Expression = { [Version]($_.Name -replace '-\d+$', '') }; Descending = $true } |
            ForEach-Object { Join-Path $_.FullName 'MpCmdRun.exe' })
    }
    $fallback = [IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'Windows Defender'))
    $candidates += Join-Path $fallback 'MpCmdRun.exe'
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $root = if ($candidate.StartsWith($platform + [IO.Path]::DirectorySeparatorChar,
                                        [StringComparison]::OrdinalIgnoreCase)) { $platform } else { $fallback }
        $full = Assert-NativePlainPath $candidate $root
        $signature = Get-AuthenticodeSignature -LiteralPath $full
        if ($signature.Status.ToString() -cne 'Valid' -or $null -eq $signature.SignerCertificate -or
            $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') {
            throw 'NATIVE_SCANNER_SIGNATURE_INVALID'
        }
        return $full
    }
    throw 'NATIVE_DEFENDER_UNAVAILABLE'
}

function Invoke-NativeBoundedProcess([string]$Program, [string[]]$Arguments,
                                     [string]$LogPrefix, [int]$Seconds) {
    $process = $null
    try {
        $quoted = ($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
        $process = Start-Process -FilePath $Program -ArgumentList $quoted -PassThru -NoNewWindow `
            -RedirectStandardOutput ($LogPrefix + '.stdout') -RedirectStandardError ($LogPrefix + '.stderr')
        if (-not $process.WaitForExit($Seconds * 1000)) {
            try { $process.Kill($true) } catch { try { $process.Kill() } catch { } }
            if (-not $process.WaitForExit(5000)) { throw 'NATIVE_PROCESS_TERMINATION_FAILED' }
            throw 'NATIVE_PROCESS_TIMEOUT'
        }
        $process.Refresh()
        return $process.ExitCode
    } finally {
        if ($null -ne $process) { $process.Dispose() }
    }
}

function Get-NativeScopedThreatIds($Detections, [string]$Root, [string]$Exe) {
    $ids = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($detection in @($Detections)) {
        if ($null -eq $detection) { continue }
        foreach ($resource in @(Get-NativeField $detection 'Resources')) {
            if ($resource -is [string] -and
                ($resource.IndexOf($Root + [IO.Path]::DirectorySeparatorChar,
                                   [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                 $resource.IndexOf($Exe, [StringComparison]::OrdinalIgnoreCase) -ge 0)) {
                $id = [string](Get-NativeField $detection 'DetectionID')
                if (-not $id) { throw 'NATIVE_DEFENDER_DETECTION_INVALID' }
                [void]$ids.Add($id)
                break
            }
        }
    }
    # Prevent PowerShell from unrolling the collection and changing the return type.
    return ,$ids
}
# End native payload helper functions.

# Native audit runner. No code below this marker is used by fixture tests.
$scratch = $null
$finalized = $false
$failure = $null
$windows = [ordered]@{
    platform = 'win32'; executable_sha256 = $null; signatures = @()
    antivirus_scan_completed = $false; antivirus_detections_count = 0
    antivirus_scanned_files_count = 0; scan_scope_files_sha256 = $null
    scanner = 'Microsoft Defender Antivirus'; scanner_version = $null
    antivirus_signature_version = $null; signature_updated_at = $null
    scan_started_at = $null; scan_finished_at = $null; scan_duration_seconds = 0.0
    am_running_mode = $null; real_time_protection_enabled = $false
    sample_submission_consent = -1; maps_reporting = -1
}
try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'NATIVE_WINDOWS_REQUIRED' }
    $exe = (Resolve-Path -LiteralPath $Executable).Path
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'NATIVE_EXECUTABLE_MISSING' }
    $outputPath = [IO.Path]::GetFullPath($Output)
    $outputDirectory = [IO.Path]::GetDirectoryName($outputPath)
    [void](New-Item -ItemType Directory -Path $outputDirectory -Force)
    if (-not $Inventory) { $Inventory = Join-Path $outputDirectory 'native-payload-inventory.json' }
    $inventoryPath = [IO.Path]::GetFullPath($Inventory)
    if ([IO.Path]::GetExtension($outputPath) -ine '.json' -or
        [IO.Path]::GetExtension($inventoryPath) -ine '.json' -or
        [string]::Equals($outputPath, $inventoryPath, [StringComparison]::OrdinalIgnoreCase) -or
        [string]::Equals($outputPath, $exe, [StringComparison]::OrdinalIgnoreCase) -or
        [string]::Equals($inventoryPath, $exe, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'NATIVE_OUTPUT_PATH_COLLISION'
    }
    [void](New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($inventoryPath)) -Force)
    $python = Resolve-NativeApplication $PythonExecutable
    $auditor = Join-Path $PSScriptRoot 'audit_native_payload.py'
    $scratch = Join-Path ([IO.Path]::GetTempPath()) ('InkPick-native-audit-' + [guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $scratch)
    $scanRoot = Join-Path $scratch 'scan'
    $staticPath, $windowsPath = (Join-Path $scratch 'static.json'), (Join-Path $scratch 'windows.json')
    $windows.executable_sha256 = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    $staticCode = Invoke-NativeBoundedProcess $python @($auditor, '--executable', $exe,
        '--extract-dir', $scanRoot, '--inventory', $inventoryPath, '--output', $staticPath,
        '--source-commit', $SourceCommit) (Join-Path $scratch 'static') $TimeoutSeconds
    if ($staticCode -ne 0) { throw 'NATIVE_STATIC_AUDIT_FAILED' }
    $static = Get-Content -LiteralPath $staticPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $inventoryReport = Get-Content -LiteralPath $inventoryPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $nativeFiles = @(Get-NativeField $inventoryReport 'files')
    $relativePaths = Get-NativeField $static 'scan_relative_paths'
    $scopeCount = Assert-NativeScanScope $scanRoot $relativePaths
    if ($nativeFiles.Count -ne $scopeCount) { throw 'NATIVE_INCOMPLETE_SCAN_SCOPE' }
    $windows.scan_scope_files_sha256 = Get-NativeField $static 'scan_scope_files_sha256'
    if ($windows.scan_scope_files_sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'NATIVE_SCOPE_HASH_INVALID' }
    $signatures = @()
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($file in $nativeFiles) {
        $relative = [string](Get-NativeField $file 'scan_path')
        if ($relativePaths -cnotcontains $relative -or -not $seen.Add($relative)) {
            throw 'NATIVE_INCOMPLETE_SCAN_SCOPE'
        }
        $path = Assert-NativePlainPath (Join-Path $scanRoot $relative) $scanRoot
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -cne (Get-NativeField $file 'sha256')) { throw 'NATIVE_SCAN_FILE_CHANGED' }
        try { $status = (Get-AuthenticodeSignature -LiteralPath $path).Status.ToString() }
        catch { $status = 'Unavailable' }
        $signatures += [ordered]@{ path = Get-NativeField $file 'path'; sha256 = $hash; status = $status }
    }
    $windows.signatures = @($signatures)
    $readiness = Get-NativeFreshDefenderReadiness -LogPrefix (Join-Path $scratch 'definition-update')
    foreach ($key in $readiness.Keys) { $windows[$key] = $readiness[$key] }
    $scanner = Get-NativeDefenderScanner
    $before = Get-NativeScopedThreatIds @(Get-MpThreatDetection) $scanRoot $exe
    $started = [DateTime]::UtcNow
    $windows.scan_started_at = $started.ToString('o')
    $scanCode = Invoke-NativeBoundedProcess $scanner @('-Scan', '-ScanType', '3', '-File', $scanRoot,
        '-DisableRemediation', '-Timeout', '1') (Join-Path $scratch 'defender') $TimeoutSeconds
    $finished = [DateTime]::UtcNow
    $windows.scan_finished_at = $finished.ToString('o')
    $windows.scan_duration_seconds = [Math]::Round(($finished - $started).TotalSeconds, 6)
    $after = Get-NativeScopedThreatIds @(Get-MpThreatDetection) $scanRoot $exe
    $windows.antivirus_detections_count = @($after | Where-Object { -not $before.Contains($_) }).Count
    # DisableRemediation prevents the 'malware successfully remediated' case
    # from masquerading as a clean return code. Nonzero may also be an error.
    if ($scanCode -ne 0) { throw 'NATIVE_DEFENDER_SCAN_FAILED' }
    $windows.antivirus_scan_completed = $true
    $windows.antivirus_scanned_files_count = $scopeCount
    [void](Assert-NativeScanScope $scanRoot $relativePaths)
    foreach ($file in $nativeFiles) {
        $path = Assert-NativePlainPath (Join-Path $scanRoot $file.scan_path) $scanRoot
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $file.sha256) {
            throw 'NATIVE_SCAN_FILE_CHANGED'
        }
    }
    if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() -cne $windows.executable_sha256) {
        throw 'NATIVE_EXECUTABLE_CHANGED'
    }
    # Security intelligence must remain usable and fresh for the completed scan.
    $finishedReadiness = Assert-NativeDefenderReadiness (Get-MpComputerStatus) (Get-MpPreference) ([DateTime]::UtcNow)
    foreach ($key in @('scanner_version', 'antivirus_signature_version', 'signature_updated_at')) {
        if ($finishedReadiness[$key] -cne $windows[$key]) { throw 'NATIVE_SCANNER_CHANGED_DURING_SCAN' }
    }
    if ($windows.antivirus_detections_count -ne 0) { throw 'NATIVE_DEFENDER_DETECTION' }
} catch {
    $failure = if ($_.Exception.Message -cmatch '^NATIVE_[A-Z_]+$') { $_.Exception.Message } else { 'NATIVE_ASSESSMENT_FAILED' }
} finally {
    if ($null -ne $scratch) {
        try {
            Write-NativeJson $windowsPath $windows
            if ((Test-Path -LiteralPath $staticPath -PathType Leaf) -and
                (Test-Path -LiteralPath $inventoryPath -PathType Leaf)) {
                $code = Invoke-NativeBoundedProcess $python @($auditor, '--finalize', $staticPath,
                    '--windows-report', $windowsPath, '--inventory', $inventoryPath,
                    '--executable', $exe, '--output', $outputPath) (Join-Path $scratch 'finalize') $TimeoutSeconds
                if ($code -eq 0) {
                    $result = Get-Content -LiteralPath $outputPath -Raw -Encoding UTF8 | ConvertFrom-Json
                    if ($result.probe -cne 'native_payload_audit' -or $result.schema -ne 1 -or
                        $result.passed -isnot [bool] -or -not $result.passed -or
                        $result.source_commit -cne $SourceCommit -or
                        $result.executable_sha256 -cne $windows.executable_sha256) {
                        throw 'NATIVE_FINAL_REPORT_MISMATCH'
                    }
                    $finalized = $true
                }
                elseif ($null -eq $failure) { $failure = 'NATIVE_FINAL_ASSESSMENT_FAILED' }
            }
        } catch {
            if ($null -eq $failure) { $failure = 'NATIVE_REPORT_FAILED' }
        } finally {
            try { Remove-Item -LiteralPath $scratch -Recurse -Force }
            catch { $failure = 'NATIVE_CLEANUP_FAILED'; $finalized = $false }
        }
    }
}
if (-not $finalized -or $null -ne $failure) {
    # A stale success report may never survive a later failure/cleanup error.
    if ($null -ne $failure -and (Test-Path -LiteralPath ([IO.Path]::GetFullPath($Output)) -PathType Leaf)) {
        try {
            $failedReport = Get-Content -LiteralPath $Output -Raw -Encoding UTF8 | ConvertFrom-Json
            $failedReport.passed = $false
            Write-NativeJson ([IO.Path]::GetFullPath($Output)) $failedReport
        } catch { Remove-Item -LiteralPath $Output -Force -ErrorAction SilentlyContinue }
    }
    [Console]::Error.WriteLine($(if ($null -ne $failure) { $failure } else { 'NATIVE_ASSESSMENT_FAILED' }))
    exit 1
}
Write-Output 'Native payload assessment passed (local inventory and antivirus scan).'
exit 0
