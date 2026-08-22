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
$appPath = Join-Path $smokeRoot "app"
$localAppDataPath = Join-Path $smokeRoot "local-app-data"
$copiedExe = Join-Path $appPath "master-duel-recorder-lite-gui.exe"
New-Item -ItemType Directory -Path $smokeRoot | Out-Null
New-Item -ItemType Directory -Path $appPath | Out-Null
Copy-Item -LiteralPath $resolvedExe -Destination $copiedExe
$previousLocalAppData = $env:LOCALAPPDATA
$env:LOCALAPPDATA = $localAppDataPath

try {
    $process = Start-Process -FilePath $copiedExe -ArgumentList @(
        "--smoke-test",
        "--smoke-output", $resultPath
    ) -PassThru -Wait
    if ($process.ExitCode -ne 0) {
        throw "GUI smoke failed with exit code $($process.ExitCode)"
    }
    if (-not (Test-Path -LiteralPath $resultPath)) {
        throw "GUI smoke result was not created"
    }
    $result = Get-Content -Raw -Encoding UTF8 -LiteralPath $resultPath | ConvertFrom-Json
    $requiredWidgets = @(
        "activity", "catalog_table", "ffmpeg_setup", "history_delete", "history_duel", "history_play", "history_table", "incomplete_duel_count", "prepare_table", "visual_details_toggle", "visual_diagnostics_folder", "visual_status",
        "data_backup_table", "data_protection_status", "history_duplicates", "history_refresh", "record_start", "record_status", "record_stop", "season_table", "settings_form", "statistics_chart",
        "statistics_date_from_picker", "statistics_date_to_picker", "statistics_deck_table", "statistics_filters", "statistics_order_table", "target_selector",
        "watch_toggle", "clean_uninstall"
    )
    if ($result.version -ne $ExpectedVersion -or $result.width -lt 900 -or $result.height -lt 600 -or -not $result.history_refresh_visible -or -not $result.calendar_contract) {
        throw "GUI smoke contract is invalid"
    }
    $expectedRuntimeData = Join-Path $localAppDataPath "MasterDuelRecorderLite"
    if ([System.IO.Path]::GetFullPath($result.runtime_data) -ne [System.IO.Path]::GetFullPath($expectedRuntimeData)) {
        throw "GUI default runtime path is invalid: $($result.runtime_data)"
    }
    foreach ($widget in $requiredWidgets) {
        if ($result.widgets -notcontains $widget) {
            throw "GUI smoke is missing widget: $widget"
        }
    }
    if (Test-Path -LiteralPath (Join-Path $appPath "user_data")) {
        throw "GUI smoke created user_data next to the EXE"
    }
    if (Test-Path -LiteralPath $expectedRuntimeData) {
        throw "read-only GUI smoke unexpectedly created runtime data"
    }
    Write-Output "GUI EXE smoke passed: $resolvedExe"
}
finally {
    $env:LOCALAPPDATA = $previousLocalAppData
    Remove-Item -LiteralPath $smokeRoot -Recurse -Force -ErrorAction SilentlyContinue
}
