from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StandardGuiFeature:
    key: str
    label: str
    required_widgets: tuple[str, ...]
    baseline_reference: str


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
        ("deck_catalog_table", "catalog_table"),
        "docs/assets/tkinter-ui-baseline-1.5.2/04-decks.png",
    ),
    StandardGuiFeature(
        "tag_catalog",
        "タグ名、説明、カラー、デッキ専用タグ管理",
        ("tag_catalog_table",),
        "docs/assets/tkinter-ui-baseline-1.5.2/05-tags.png",
    ),
    StandardGuiFeature(
        "season_management",
        "シーズン追加・更新・削除/アーカイブ、期間、レポート",
        ("season_table",),
        "docs/assets/tkinter-ui-baseline-1.5.2/06-seasons.png",
    ),
    StandardGuiFeature(
        "youtube_template",
        "YouTube投稿テンプレートと投稿状態管理",
        (
            "youtube_template",
            "youtube_status",
            "youtube_connect",
            "youtube_disconnect",
            "youtube_refresh",
            "youtube_test_upload",
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
        ("reliability_status", "improvement_status"),
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
        ("data_protection_status", "data_backup_table", "clean_uninstall"),
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


def required_standard_widget_keys() -> tuple[str, ...]:
    return tuple(
        sorted({widget for feature in STANDARD_GUI_FEATURES for widget in feature.required_widgets})
    )


def satisfied_standard_feature_keys(widget_keys: set[str]) -> tuple[str, ...]:
    return tuple(
        feature.key
        for feature in STANDARD_GUI_FEATURES
        if all(widget in widget_keys for widget in feature.required_widgets)
    )
