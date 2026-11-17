param(
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][string]$Report
)
$ErrorActionPreference = 'Stop'
$hostPython = (Get-Command python -ErrorAction Stop).Source
& $hostPython (Join-Path $PSScriptRoot 'benchmark_resource_budget.py') --executable $Executable --report $Report
if ($LASTEXITCODE -ne 0) { throw 'Actual candidate resource budget validation failed' }
