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
        "activity", "app_update", "catalog_table", "clean_uninstall", "csv_status", "data_backup_table", "data_protection_scope", "data_protection_status", "deck_catalog_table", "ffmpeg_setup",
        "history_add", "history_bulk", "history_columns", "history_delete", "history_duel", "history_duplicates", "history_incomplete", "history_play", "history_refresh", "history_table", "history_youtube",
        "improvement_status", "incomplete_duel_count", "manual_duel_add", "prepare_recording", "prepare_table", "record_start", "record_status", "record_stop", "reliability_status",
        "season_table", "settings_audio_input", "settings_audio_mode", "settings_audio_test", "settings_csv_export", "settings_data_backup", "settings_display_colors", "settings_ffmpeg_path", "settings_form", "settings_managed_export", "settings_runtime_path", "settings_save", "settings_youtube_connect", "settings_youtube_disconnect", "settings_youtube_refresh", "settings_youtube_status", "settings_youtube_test_upload", "statistics_chart", "statistics_coin_table", "statistics_date_from_picker", "statistics_date_to_picker", "statistics_deck_table", "statistics_filters", "statistics_order_table", "statistics_season_table",
        "tag_catalog_table", "target_selector", "visual_details_toggle", "visual_diagnostics_folder", "visual_status", "watch_toggle",
        "youtube_background_status", "youtube_status", "youtube_template", "youtube_template_save", "youtube_template_tags", "youtube_template_title", "youtube_upload_progress"
    )
    if ($result.version -ne $ExpectedVersion -or $result.width -lt 900 -or $result.height -lt 600 -or -not $result.history_refresh_visible -or -not $result.calendar_contract -or -not $result.pyside6 -or -not $result.standard_feature_contract -or -not $result.standard_operation_contract) {
        throw "GUI smoke contract is invalid"
    }
    if ($result.gui_entrypoint -ne "master_duel_recorder_lite.pyside_gui") {
        throw "GUI smoke entrypoint is invalid: $($result.gui_entrypoint)"
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
    if ($result.failed_standard_operation_checks.Count -ne 0) {
        throw "GUI smoke has failed operation checks"
    }
    if (-not $result.settings_parity_contract) {
        throw "GUI smoke settings parity contract is invalid"
    }
    if (-not $result.app_update_state_contract.download_enabled_only_after_candidate -or -not $result.app_update_state_contract.latest_without_candidate_disables_download) {
        throw "GUI smoke app update state contract is invalid"
    }
    if (-not $result.template_screen_contract.connection_buttons_removed) {
        throw "GUI smoke template screen contract is invalid"
    }
    if (-not $result.background_operation_contract.youtube_upload_worker -or -not $result.background_operation_contract.double_submit_guard) {
        throw "GUI smoke background operation contract is invalid"
    }
    if ($result.background_operation_contract.progress_widget -ne "youtube_upload_progress") {
        throw "GUI smoke background progress widget is invalid"
    }
    if (-not $result.reliability_action_contract.click_updates_status) {
        throw "GUI smoke reliability action contract is invalid"
    }
    foreach ($key in @("history_hub", "incomplete_action", "play_action", "edit_action", "danger_delete_action", "duplicate_review", "youtube_action", "timeline_entry", "diagnostic_entry", "review_entry")) {
        if (-not $result.post_recording_workflow_contract.$key) {
            throw "GUI smoke post-recording workflow contract is invalid: $key"
        }
    }
    foreach ($key in @("status_visible", "scope_visible", "backup_table_visible", "clean_uninstall_guard", "recordings_excluded_text", "queue_manifest_oauth_excluded_text", "runtime_database_path_present")) {
        if (-not $result.data_protection_display_contract.$key) {
            throw "GUI smoke data protection display contract is invalid: $key"
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
