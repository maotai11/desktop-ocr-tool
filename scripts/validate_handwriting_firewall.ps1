# CI-only candidate check. This script temporarily changes outbound firewall rules.
# Run only with explicit authorization; the distributed application never runs it.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundleDirectory,
    [Parameter(Mandatory = $true)][string]$ReportDirectory,
    [ValidateRange(30, 300)][int]$TimeoutSeconds = 120
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ruleNames = @()
$process = $null
$summary = [ordered]@{
    schema = 1; passed = $false; clean_machine_verified = $false
    helper_outbound_blocked = $false; executable_outbound_blocked = $false
    firewall_service_running = $false; firewall_profiles_enabled = $false
    network_observation = 'OUTBOUND_BLOCKED_NOT_PACKET_TRACE'
}
function Add-ExactBlock([string]$Path) {
    $name = 'InkPick-handwriting-' + [guid]::NewGuid().ToString()
    $script:ruleNames += $name
    New-NetFirewallRule -Name $name -DisplayName $name -Direction Outbound -Program $Path -Action Block -Profile Any | Out-Null
    $rule = Get-NetFirewallRule -PolicyStore ActiveStore -Name $name
    $filter = $rule | Get-NetFirewallApplicationFilter
    if ($rule.Enabled -ne 'True' -or $rule.Direction -ne 'Outbound' -or $rule.Action -ne 'Block' -or
        -not [string]::Equals([IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($filter.Program)),
                             $Path, [StringComparison]::OrdinalIgnoreCase)) { throw 'Exact outbound block could not be verified' }
}
try {
    if ((Get-Service -Name MpsSvc).Status -ne 'Running') { throw 'Windows Firewall service is not enforcing' }
    $profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore)
    if ($profiles.Count -ne 3 -or @($profiles | Where-Object { $_.Enabled -ne 'True' }).Count -ne 0) {
        throw 'Windows Firewall profiles are not all enabled; no system setting will be changed'
    }
    $summary.firewall_service_running = $true
    $summary.firewall_profiles_enabled = $true
    $bundle = (Resolve-Path -LiteralPath $BundleDirectory).Path
    $reports = New-Item -ItemType Directory -Path $ReportDirectory -Force
    $reportPath = Join-Path $reports.FullName 'frozen-handwriting.json'
    $startPath = $reportPath + '.start'
    foreach ($path in @($reportPath, $startPath)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
    }
    $manifest = Get-Content -LiteralPath (Join-Path $bundle 'BUILD_MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schema -ne 2 -or $manifest.platform -cne 'win32' -or $manifest.version -cne '1.8.0-rc.2' -or
        $manifest.executable_name -cne 'InkPick-v1.8.0-rc.2.exe') { throw 'Unexpected fixed candidate identity' }
    $exe = Join-Path $bundle $manifest.executable_name
    $exeHash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
    $helperHash = $manifest.handwriting.helper.sha256
    if ($exeHash -cne $manifest.executable_sha256 -or $helperHash -cnotmatch '^[0-9a-f]{64}$') {
        throw 'Candidate executable/helper identity mismatch'
    }
    Add-ExactBlock $exe
    $summary.executable_outbound_blocked = $true
    $summary.executable_sha256 = $exeHash
    $summary.helper_sha256 = $helperHash
    $summary.version = $manifest.version
    $process = Start-Process -FilePath $exe -ArgumentList @('--self-test-handwriting', "`"$reportPath`"", '--wait-for-firewall') -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Min(40, $TimeoutSeconds / 2))
    $waiting = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($process.HasExited) { throw 'Candidate exited before helper firewall handshake' }
        if (Test-Path -LiteralPath $reportPath) {
            try { $candidate = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $candidate = $null }
            if ($null -ne $candidate -and $candidate.phase -ceq 'waiting_for_firewall') { $waiting = $candidate; break }
        }
        Start-Sleep -Milliseconds 100
    }
    if ($null -eq $waiting -or $waiting.frozen -ne $true -or $waiting.platform -cne 'win32' -or
        $waiting.executable_sha256 -cne $exeHash -or $waiting.helper_sha256 -cne $helperHash -or
        $waiting.version -cne $manifest.version -or $waiting.firewall_nonce -cnotmatch '^[0-9a-f]{32}$') {
        throw 'Missing or mismatched frozen helper handshake'
    }
    $helperPath = [IO.Path]::GetFullPath($waiting.helper_path)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if (-not $helperPath.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $helperPath.Substring($tempRoot.Length) -cnotmatch '^_MEI[^\\/]+\\native\\handwriting\\runtime\\inkpick-handwriting\.exe$') {
        throw 'Helper is not in the current one-file extraction layout'
    }
    $node = Get-Item -LiteralPath $helperPath
    while ($null -ne $node -and $node.FullName.Length -ge $tempRoot.Length) {
        if ($node.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Redirected extracted helper path' }
        $node = if ($node -is [IO.FileInfo]) { $node.Directory } else { $node.Parent }
        if ($null -eq $node) { break }
    }
    if ((Get-FileHash -LiteralPath $helperPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $helperHash) {
        throw 'Extracted helper hash mismatch'
    }
    Add-ExactBlock $helperPath
    $summary.helper_outbound_blocked = $true
    @{ nonce = $waiting.firewall_nonce; helper_sha256 = $helperHash } | ConvertTo-Json -Compress |
        Set-Content -LiteralPath $startPath -Encoding UTF8
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) { throw 'Frozen handwriting probe timed out' }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw 'Frozen handwriting probe failed' }
    $result = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($result.passed -ne $true -or $result.phase -cne 'completed' -or $result.frozen -ne $true -or
        $result.qt_platform -cne 'windows' -or $result.executable_sha256 -cne $exeHash -or
        $result.helper_sha256 -cne $helperHash -or $result.version -cne $manifest.version -or
        $result.firewall_gate_acknowledged -ne $true -or $result.helper_stopped -ne $true -or
        $result.job_assigned -ne $true -or $result.clean_machine_verified -ne $false) {
        throw 'Incomplete or mismatched frozen handwriting result'
    }
    $summary.report_sha256 = (Get-FileHash -LiteralPath $reportPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ((Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() -cne $exeHash) {
        throw 'Outer executable changed during validation'
    }
    $summary.passed = $true
} catch {
    $summary.error = 'HANDWRITING_FIREWALL_VALIDATION_FAILED'
    Write-Warning $_.Exception.Message
} finally {
    if ($null -ne $process -and -not $process.HasExited) {
        & taskkill.exe /PID $process.Id /T /F | Out-Null
        $summary.passed = $false
    }
    foreach ($name in $ruleNames) {
        try {
            if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) {
                Remove-NetFirewallRule -Name $name
            }
            if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) { throw 'Temporary block remained' }
        } catch {
            $summary.passed = $false
            $summary.error = 'HANDWRITING_FIREWALL_CLEANUP_FAILED'
        }
    }
    if (Test-Path -LiteralPath $ReportDirectory -PathType Container) {
        $summary | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $ReportDirectory 'handwriting-firewall-gate.json') -Encoding UTF8
    }
}
if (-not $summary.passed) { exit 1 }
exit 0
