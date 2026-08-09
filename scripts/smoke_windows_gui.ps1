param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedVersion
)

$ErrorActionPreference = "Stop"
$resolvedExe = (Resolve-Path -LiteralPath $ExePath).Path
$smokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("mdrl-gui-smoke-" + [guid]::NewGuid().ToString("N"))
$resultPath = Join-Path $smokeRoot "result.json"
$userDataPath = Join-Path $smokeRoot "user_data"
New-Item -ItemType Directory -Path $smokeRoot | Out-Null

try {
    $process = Start-Process -FilePath $resolvedExe -ArgumentList @(
        "--smoke-test",
        "--smoke-output", $resultPath,
        "--user-data-dir", $userDataPath
    ) -PassThru -Wait
    if ($process.ExitCode -ne 0) {
        throw "GUI smoke failed with exit code $($process.ExitCode)"
    }
    if (-not (Test-Path -LiteralPath $resultPath)) {
        throw "GUI smoke result was not created"
    }
    $result = Get-Content -Raw -Encoding UTF8 -LiteralPath $resultPath | ConvertFrom-Json
    $requiredWidgets = @(
        "activity", "history_diagnostic", "history_duel", "history_play", "history_reveal", "history_table", "prepare_table",
        "record_start", "record_stop", "recovery_table", "settings_form", "target_selector",
        "watch_toggle"
    )
    if ($result.version -ne $ExpectedVersion -or $result.width -lt 900 -or $result.height -lt 600) {
        throw "GUI smoke contract is invalid"
    }
    foreach ($widget in $requiredWidgets) {
        if ($result.widgets -notcontains $widget) {
            throw "GUI smoke is missing widget: $widget"
        }
    }
    if (Test-Path -LiteralPath $userDataPath) {
        throw "GUI smoke unexpectedly created user_data"
    }
    Write-Output "GUI EXE smoke passed: $resolvedExe"
}
finally {
    Remove-Item -LiteralPath $smokeRoot -Recurse -Force -ErrorAction SilentlyContinue
}
