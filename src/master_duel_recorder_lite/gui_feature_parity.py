from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StandardGuiFeature:
    key: str
    label: str
    required_widgets: tuple[str, ...]
    baseline_reference: str


@dataclass(frozen=True)
class StandardGuiOperationCheck:
    feature_key: str
    operation_key: str
    operation_label: str
    target_state: str
    required_widgets: tuple[str, ...]
    expected_result: str
    failure_display: str


STANDARD_GUI_FEATURES: tuple[StandardGuiFeature, ...] = (
    StandardGuiFeature(
        "recording_control",
        "録画対象選択、録画開始・停止、自動監視、環境診断",
        (
            "target_selector",
            "record_start",
            "record_stop",
            "watch_toggle",
            "record_status",
            "visual_status",
            "visual_details_toggle",
            "visual_diagnostics_folder",
            "activity",
        ),
        "docs/assets/tkinter-ui-baseline-1.5.2/01-record.png",
    ),
    StandardGuiFeature(
        "manual_duel_entry",
        "録画なし戦績の手動追加と簡易入力",
        ("manual_duel_add", "history_add"),
        "docs/assets/tkinter-ui-baseline-1.5.2-popups/07-quick-duel-editor.png",
    ),
    StandardGuiFeature(
        "history_management",
        "戦績管理一覧、未完了処理、一括編集、再生、編集、削除、重複比較、更新",
        (
            "history_table",
            "history_incomplete",
            "history_bulk",
            "history_play",
            "history_duel",
            "history_delete",
            "history_duplicates",
            "history_refresh",
        ),
        "docs/assets/tkinter-ui-baseline-1.5.2/02-history.png",
    ),
    StandardGuiFeature(
        "history_filter_columns",
        "戦績管理のフィルター、表示列、YouTube投稿導線",
        ("history_columns", "history_youtube"),
        "docs/assets/tkinter-ui-baseline-1.5.2-popups/04-history-filter.png",
    ),
    StandardGuiFeature(
        "statistics",
        "全体・条件付き・先後・デッキ・コイントス・シーズン統計",
        (
            "statistics_filters",
            "statistics_chart",
            "statistics_deck_table",
            "statistics_order_table",
            "statistics_coin_table",
            "statistics_season_table",
            "statistics_date_from_picker",
            "statistics_date_to_picker",
        ),
        "docs/assets/tkinter-ui-baseline-1.5.2/03-statistics.png",
    ),
    StandardGuiFeature(
        "deck_catalog",
        "デッキ名、説明、カラー、用途、使用回数、デッキタグ管理",
        (
            "deck_catalog_table",
            "catalog_table",
            "deck_name_input",
            "deck_add",
            "deck_save",
            "deck_delete",
        ),
        "docs/assets/tkinter-ui-baseline-1.5.2/04-decks.png",
    ),
    StandardGuiFeature(
        "tag_catalog",
        "タグ名、説明、カラー、デッキ専用タグ管理",
        (
            "tag_catalog_table",
            "tag_name_input",
            "tag_add",
            "tag_save",
            "tag_delete",
        ),
        "docs/assets/tkinter-ui-baseline-1.5.2/05-tags.png",
    ),
    StandardGuiFeature(
        "season_management",
        "シーズン追加・更新・削除/アーカイブ、期間、レポート",
        (
            "season_table",
            "season_name_input",
            "season_add",
            "season_save",
            "season_archive",
            "season_report",
        ),
        "docs/assets/tkinter-ui-baseline-1.5.2/06-seasons.png",
    ),
    StandardGuiFeature(
        "youtube_template",
        "YouTube投稿テンプレートと投稿準備状態管理",
        (
            "youtube_template",
            "youtube_status",
            "youtube_template_title",
            "youtube_template_tags",
            "youtube_template_save",
            "youtube_background_status",
            "youtube_upload_progress",
        ),
        "docs/assets/tkinter-ui-baseline-1.5.2/07-template.png",
    ),
    StandardGuiFeature(
        "prepare_queue",
        "MP4準備、投稿前処理キュー、準備対象選択",
        ("prepare_table", "prepare_recording"),
        "docs/assets/tkinter-ui-baseline-1.5.2/10-prepare-internal.png",
    ),
    StandardGuiFeature(
        "reliability",
        "事前チェック、導入状態、ホットキー、トレイ、後解析導線",
        (
            "reliability_status",
            "reliability_refresh",
            "reliability_setup_check",
            "improvement_status",
        ),
        "docs/assets/tkinter-ui-baseline-1.5.2/08-reliability.png",
    ),
    StandardGuiFeature(
        "settings_recording_audio",
        "FFmpeg、音声、録画品質、自動判定、通知、保存先設定",
        ("settings_form", "ffmpeg_setup"),
        "docs/assets/tkinter-ui-baseline-1.5.2/09-settings.png",
    ),
    StandardGuiFeature(
        "data_protection",
        "管理データ、バックアップ、復元、整合性診断、クリーンアンインストール",
        (
            "data_protection_status",
            "data_protection_scope",
            "data_backup_table",
            "clean_uninstall",
        ),
        "docs/assets/tkinter-ui-baseline-1.5.2-popups/13-clean-uninstall.png",
    ),
    StandardGuiFeature(
        "csv_and_update",
        "戦績CSV入出力、アプリ更新確認、更新適用",
        ("csv_status", "app_update"),
        "docs/assets/tkinter-ui-baseline-1.5.2/09-settings.png",
    ),
    StandardGuiFeature(
        "dialogs",
        "日付選択、FFmpeg導入、投稿、フィルター、タイムライン、戦績入力、診断、レポート",
        (
            "statistics_date_from_picker",
            "ffmpeg_setup",
            "history_youtube",
            "history_duplicates",
            "history_incomplete",
            "history_bulk",
        ),
        "docs/assets/tkinter-ui-baseline-1.5.2-popups",
    ),
)


STANDARD_GUI_OPERATION_CHECKS: tuple[StandardGuiOperationCheck, ...] = (
    StandardGuiOperationCheck(
        "recording_control",
        "record_start_stop",
        "録画対象を選んで録画開始・停止へ到達できる",
        "通常録画可能状態",
        ("target_selector", "record_start", "record_stop", "record_status"),
        "録画開始、停止、現在状態を同じ操作面で確認できる",
        "録画対象、開始、停止、状態表示の不足を操作名で表示する",
    ),
    StandardGuiOperationCheck(
        "recording_control",
        "recording_diagnostics",
        "録画環境診断と検出詳細へ到達できる",
        "録画前の環境確認",
        ("visual_status", "visual_details_toggle", "visual_diagnostics_folder"),
        "検出状態、詳細、診断フォルダーを確認できる",
        "診断へ進めない理由を録画環境の不足として表示する",
    ),
    StandardGuiOperationCheck(
        "manual_duel_entry",
        "manual_duel_add",
        "録画なし戦績を手動追加できる",
        "録画ファイルなしで戦績を残す状態",
        ("manual_duel_add", "history_add"),
        "戦績管理から手動追加または簡易入力へ進める",
        "手動追加入口の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "history_management",
        "post_recording_hub",
        "戦績管理から未完了処理、再生、編集、削除へ進める",
        "録画後整理状態",
        (
            "history_table",
            "history_incomplete",
            "history_play",
            "history_duel",
            "history_delete",
            "history_refresh",
        ),
        "行選択後に主操作と危険操作を区別して実行できる",
        "対象行、未完了処理、再生、編集、削除、更新の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "history_management",
        "bulk_and_duplicates",
        "一括編集と重複候補比較へ進める",
        "複数戦績整理状態",
        ("history_table", "history_bulk", "history_duplicates"),
        "複数行の整理と重複候補確認へ進める",
        "一括編集または重複比較の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "history_filter_columns",
        "filter_columns_youtube",
        "フィルター、表示列、YouTube投稿導線へ進める",
        "履歴一覧の絞り込みと投稿準備状態",
        ("history_columns", "history_youtube"),
        "一覧の見え方を調整し、投稿対象行の投稿導線へ進める",
        "表示列またはYouTube投稿入口の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "statistics",
        "statistics_review",
        "条件付き統計と集計表を確認できる",
        "DB入りruntimeまたは空runtime",
        (
            "statistics_filters",
            "statistics_chart",
            "statistics_deck_table",
            "statistics_order_table",
            "statistics_coin_table",
            "statistics_season_table",
        ),
        "勝敗、先後、コイントス、デッキ、シーズンの集計へ到達できる",
        "統計フィルターまたは集計表の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "deck_catalog",
        "deck_catalog_review",
        "デッキ一覧と編集保存へ進める",
        "デッキ管理状態",
        ("deck_catalog_table", "catalog_table", "deck_name_input", "deck_save"),
        "デッキ名、色、用途、使用回数を確認し、選択行を編集保存できる",
        "デッキ管理テーブルまたは編集入口の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "tag_catalog",
        "tag_catalog_review",
        "タグ一覧と編集保存へ進める",
        "タグ管理状態",
        ("tag_catalog_table", "tag_name_input", "tag_save"),
        "タグ名、説明、色、デッキ専用タグを確認し、選択行を編集保存できる",
        "タグ管理テーブルまたは編集入口の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "season_management",
        "season_report_review",
        "シーズン一覧、編集保存、レポートへ進める",
        "シーズン管理状態",
        ("season_table", "season_name_input", "season_save", "season_report"),
        "期間、アーカイブ状態、シーズンレポートを確認し、選択行を編集保存できる",
        "シーズン管理テーブルまたは編集入口の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "youtube_template",
        "youtube_template_edit",
        "YouTube投稿テンプレートを編集できる",
        "OAuth未接続または接続確認状態",
        (
            "youtube_template",
            "youtube_status",
            "youtube_template_title",
            "youtube_template_tags",
            "youtube_template_save",
            "youtube_background_status",
            "youtube_upload_progress",
        ),
        "タイトル、概要欄、タグを保存でき、接続管理は設定画面で扱う",
        "YouTube投稿テンプレート編集入口の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "prepare_queue",
        "prepare_queue_review",
        "MP4準備と投稿前処理キューを確認できる",
        "投稿準備状態",
        ("prepare_table", "prepare_recording"),
        "投稿前処理対象とキュー状態を確認できる",
        "MP4準備またはキュー表示の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "reliability",
        "reliability_review",
        "事前チェックと改善導線を確認できる",
        "録画前の信頼性確認状態",
        (
            "reliability_status",
            "reliability_refresh",
            "reliability_setup_check",
            "improvement_status",
        ),
        "導入状態、ホットキー、後解析導線を確認できる",
        "信頼性表示または改善導線の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "settings_recording_audio",
        "settings_and_ffmpeg",
        "録画・音声設定とFFmpeg導入へ進める",
        "設定確認状態",
        ("settings_form", "ffmpeg_setup"),
        "保存先、録画品質、音声、FFmpeg導入状態を確認できる",
        "設定フォームまたはFFmpeg導入入口の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "data_protection",
        "data_protection_status",
        "バックアップ、復元、整合性診断、クリーンアンインストールを確認できる",
        "データ保全確認状態",
        (
            "data_protection_status",
            "data_protection_scope",
            "data_backup_table",
            "clean_uninstall",
        ),
        "保全状態、バックアップ一覧、危険操作入口を確認できる",
        "データ保全状態、バックアップ一覧、危険操作確認の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "csv_and_update",
        "csv_update_review",
        "戦績CSV入出力と更新確認へ進める",
        "データ入出力と更新確認状態",
        ("csv_status", "app_update"),
        "CSV処理状態とアプリ更新入口を確認できる",
        "CSV状態または更新入口の不足を表示する",
    ),
    StandardGuiOperationCheck(
        "dialogs",
        "required_dialogs",
        "主要ダイアログへ到達できる",
        "日常操作と失敗確認状態",
        (
            "statistics_date_from_picker",
            "ffmpeg_setup",
            "history_youtube",
            "history_duplicates",
            "history_incomplete",
            "history_bulk",
        ),
        "日付、FFmpeg、投稿、重複、未完了、一括編集の入口を確認できる",
        "必要ダイアログ入口の不足を表示する",
    ),
)


def required_standard_widget_keys() -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                widget
                for feature in STANDARD_GUI_FEATURES
                for widget in feature.required_widgets
            }
        )
    )


def satisfied_standard_feature_keys(widget_keys: set[str]) -> tuple[str, ...]:
    return tuple(
        feature.key
        for feature in STANDARD_GUI_FEATURES
        if all(widget in widget_keys for widget in feature.required_widgets)
    )


def evaluate_standard_operation_checks(
    widget_keys: set[str],
) -> tuple[dict[str, object], ...]:
    results: list[dict[str, object]] = []
    for check in STANDARD_GUI_OPERATION_CHECKS:
        missing_widgets = tuple(
            widget for widget in check.required_widgets if widget not in widget_keys
        )
        passed = not missing_widgets
        results.append(
            {
                "feature_key": check.feature_key,
                "operation_key": check.operation_key,
                "operation_label": check.operation_label,
                "target_state": check.target_state,
                "required_widgets": check.required_widgets,
                "missing_widgets": missing_widgets,
                "passed": passed,
                "expected_result": check.expected_result,
                "failure_display": "" if passed else check.failure_display,
            }
        )
    return tuple(results)
