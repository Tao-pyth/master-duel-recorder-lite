from __future__ import annotations

from pathlib import Path

from .application import RecorderApplicationService
from .season_reports import SeasonReport


def _rate(value: float | None) -> str:
    return "未算出" if value is None else f"{value * 100:.1f}%"


def report_summary_text(report: SeasonReport) -> str:
    season = report.season
    metric = report.summary.filtered
    lines = [
        season.name,
        f"期間: {season.start_date} ～ {season.end_date}",
        "対象: このシーズンに登録された、期間内の確定済み有効戦績",
        f"{metric.matches}戦 {metric.wins}勝 {metric.losses}敗 {metric.draws}分 / 勝率 {_rate(metric.win_rate)}",
    ]
    if report.small_sample:
        lines.append(f"少数標本: {report.sample_threshold}戦未満です")
    comparison = report.comparison
    if report.comparison_season is None:
        lines.append("比較: 比較なし")
    else:
        lines.append(f"既定の比較対象: {report.comparison_season.name}")
        if comparison.comparison is not None:
            previous = comparison.comparison
            lines.append(f"比較対象: {previous.matches}戦 / 勝率 {_rate(previous.win_rate)}")
        delta = "未算出" if comparison.win_rate_delta is None else f"{comparison.win_rate_delta * 100:+.1f}ポイント"
        lines.append(f"勝率差: {delta}")
    for label, value in (
        ("目標", season.report_goal),
        ("良かった点", season.report_highlights),
        ("課題", season.report_challenges),
        ("次期方針", season.report_next_plan),
        ("メモ", season.report_notes),
    ):
        lines.extend(("", label, value or "未記入"))
    lines.extend(("", "閲覧専用です。表示するたびに現在の戦績から集計します。"))
    return "\n".join(lines)


def create_season_report_dialog(parent: object, service: RecorderApplicationService, report: SeasonReport):
    # GUIを使う時だけ読み込み、PySide6なしのCLIを維持する。
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
        QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
        QTabWidget, QTextEdit, QVBoxLayout,
    )

    class SeasonReportDialog(QDialog):
        def __init__(self):
            super().__init__(parent)
            self.setObjectName("season_report_dialog")
            self.setWindowTitle("シーズンレポート")
            self.resize(920, 640)
            self.setMinimumSize(740, 480)
            self.season_id = report.season.season_id
            layout = QVBoxLayout(self)
            self.tabs = QTabWidget()
            self.summary = QTextEdit()
            self.summary.setObjectName("season_report_summary")
            self.summary.setReadOnly(True)
            self.tabs.addTab(self.summary, "概要・メモ")
            self.tables = {}
            metric_headers = ("対戦", "勝利", "敗北", "引分", "勝率", "標本")
            for key, title, headers in (
                ("decks", "デッキ・先後", ("デッキ", "先後", *metric_headers)),
                ("axes", "コイン・先後", ("区分", *metric_headers)),
                ("daily", "日別推移", ("期間", "対戦", "勝利", "敗北", "引分", "勝率", "累積勝率")),
                ("weekly", "週別推移", ("期間", "対戦", "勝利", "敗北", "引分", "勝率", "累積勝率")),
            ):
                table = QTableWidget(0, len(headers))
                table.setObjectName(f"season_report_{key}")
                table.setHorizontalHeaderLabels(headers)
                table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
                table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
                table.verticalHeader().hide()
                table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
                table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
                self.tables[key] = table
                self.tabs.addTab(table, title)
            layout.addWidget(self.tabs, 1)
            self.status = QLabel("閲覧専用 / HTML保存時にも最新の戦績から再集計します")
            self.status.setObjectName("season_report_status")
            self.status.setWordWrap(True)
            layout.addWidget(self.status)
            actions = QHBoxLayout()
            self.refresh_button = QPushButton("再集計")
            self.refresh_button.clicked.connect(self.reload_report)
            self.export_button = QPushButton("HTMLを保存")
            self.export_button.setObjectName("season_report_export")
            self.export_button.clicked.connect(self.export_html)
            self.close_button = QPushButton("閉じる")
            self.close_button.clicked.connect(self.accept)
            actions.addWidget(self.refresh_button)
            actions.addStretch(1)
            actions.addWidget(self.export_button)
            actions.addWidget(self.close_button)
            layout.addLayout(actions)
            self.set_report(report)

        def set_report(self, current):
            if current.season.season_id != self.season_id:
                raise ValueError("レポートの対象シーズンが一致しません")
            self.report = current
            self.summary.setPlainText(report_summary_text(current))

            def metric_values(metric):
                return (metric.matches, metric.wins, metric.losses, metric.draws, _rate(metric.win_rate))

            rows = {
                "decks": [(item.deck_name, item.label, *metric_values(item.metric), "少数標本" if item.small_sample else "") for item in current.deck_orders],
                "axes": [(item.label, *metric_values(item.metric), "少数標本" if item.small_sample else "") for item in current.axes],
                "daily": [(str(point.period_start), *metric_values(point.metric), _rate(point.cumulative_win_rate)) for point in current.daily_trend],
                "weekly": [(str(point.period_start), *metric_values(point.metric), _rate(point.cumulative_win_rate)) for point in current.weekly_trend],
            }
            for key, values in rows.items():
                table = self.tables[key]
                table.setRowCount(len(values))
                for row, cells in enumerate(values):
                    for column, value in enumerate(cells):
                        item = QTableWidgetItem(str(value))
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        table.setItem(row, column, item)
                    table.setRowHeight(row, 36)

        def reload_report(self, *_args) -> bool:
            try:
                self.set_report(service.get_season_report(self.season_id))
            except Exception as exc:
                self.status.setText("再集計できません。前回の表示を保持しています")
                QMessageBox.warning(self, "シーズンレポートを更新できません", str(exc))
                return False
            self.status.setText("最新の戦績から再集計しました")
            return True

        def export_html(self, *_args) -> None:
            filename, _filter = QFileDialog.getSaveFileName(
                self, "シーズンレポートを保存", f"season-{self.season_id}.html",
                "HTML (*.html *.htm)", options=QFileDialog.Option.DontConfirmOverwrite,
            )
            if not filename:
                self.status.setText("HTML保存を取り消しました")
                return
            destination = Path(filename)
            overwrite = destination.exists()
            if overwrite and QMessageBox.question(
                self, "HTMLを上書き", f"既存のファイルを上書きしますか？\n{destination}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                self.status.setText("HTML保存を取り消しました")
                return
            if not self.reload_report():
                return
            try:
                saved = service.export_season_report(self.report, destination, overwrite=overwrite)
            except Exception as exc:
                self.status.setText("HTMLを保存できませんでした")
                QMessageBox.warning(self, "HTMLを保存できません", str(exc))
                return
            self.status.setText(f"HTMLを保存しました: {saved}")

    return SeasonReportDialog()
