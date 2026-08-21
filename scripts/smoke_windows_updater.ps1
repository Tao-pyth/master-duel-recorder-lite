param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedVersion
)

$ErrorActionPreference = "Stop"
$resolvedExe = (Resolve-Path -LiteralPath $ExePath).Path

$versionOutput = & $resolvedExe --version
if ($LASTEXITCODE -ne 0) {
    throw "--version failed with exit code $LASTEXITCODE"
}
if (($versionOutput | Out-String).Trim() -ne "mdrl-updater $ExpectedVersion") {
    throw "unexpected updater version output: $versionOutput"
}

$helpOutput = & $resolvedExe --help
if ($LASTEXITCODE -ne 0) {
    throw "--help failed with exit code $LASTEXITCODE"
}
foreach ($option in @("--current", "--candidate", "--backup", "--expected-sha256", "--expected-version")) {
    if (($helpOutput | Out-String) -notmatch [regex]::Escape($option)) {
        throw "updater help output does not contain option: $option"
    }
}

Write-Output "Updater EXE smoke passed: $resolvedExe"
