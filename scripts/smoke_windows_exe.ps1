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
if (($versionOutput | Out-String).Trim() -ne "mdrl $ExpectedVersion") {
    throw "unexpected version output: $versionOutput"
}

$helpOutput = & $resolvedExe --help
if ($LASTEXITCODE -ne 0) {
    throw "--help failed with exit code $LASTEXITCODE"
}
if (($helpOutput | Out-String) -notmatch "config" -or ($helpOutput | Out-String) -notmatch "status") {
    throw "help output does not contain core commands"
}

$historyHelp = & $resolvedExe history --help
if ($LASTEXITCODE -ne 0) {
    throw "history --help failed with exit code $LASTEXITCODE"
}
if (($historyHelp | Out-String) -notmatch "play" -or ($historyHelp | Out-String) -notmatch "reveal") {
    throw "history help does not contain recording browsing commands"
}

$duelHelp = & $resolvedExe duel --help
if ($LASTEXITCODE -ne 0) {
    throw "duel --help failed with exit code $LASTEXITCODE"
}
foreach ($command in @("show", "set", "confirm", "history")) {
    if (($duelHelp | Out-String) -notmatch $command) {
        throw "duel help does not contain command: $command"
    }
}

$timelineHelp = & $resolvedExe timeline --help
if ($LASTEXITCODE -ne 0) {
    throw "timeline --help failed with exit code $LASTEXITCODE"
}
foreach ($command in @("list", "add", "confirm", "reject")) {
    if (($timelineHelp | Out-String) -notmatch $command) {
        throw "timeline help does not contain command: $command"
    }
}

$isolatedData = Join-Path ([System.IO.Path]::GetTempPath()) ("mdrl-exe-smoke-" + [guid]::NewGuid().ToString("N"))
$configJson = & $resolvedExe --user-data-dir $isolatedData config show --json
if ($LASTEXITCODE -ne 0) {
    throw "config show --json failed with exit code $LASTEXITCODE"
}
$config = ($configJson | Out-String) | ConvertFrom-Json
if ($config.schema_version -ne 1 -or $config.values."upload.privacy_status" -ne "private") {
    throw "config JSON contract is invalid"
}
if (Test-Path -LiteralPath $isolatedData) {
    throw "read-only config smoke unexpectedly created user_data"
}

Write-Output "EXE smoke passed: $resolvedExe"
