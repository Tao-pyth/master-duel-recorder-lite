from __future__ import annotations

from datetime import datetime
from html import escape
import os
from pathlib import Path
import re
import uuid

from .duel_statistics import StatisticsMetric, StatisticsTrendPoint
from .season_reports import SeasonReport


class SeasonReportExportError(RuntimeError):
    """シーズンレポートを安全にHTML出力できない場合のエラーです。"""


class SeasonReportHtmlExporter:
    def export(
        self, report: SeasonReport, destination: Path, *, overwrite: bool = False
    ) -> Path:
        path = destination.expanduser().resolve()
        _validate_destination(path)
        if path.exists() and not overwrite:
            raise SeasonReportExportError(f"出力先は既に存在します: {path.name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(render_season_report_html(report))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            return path
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise SeasonReportExportError(f"レポートを保存できません: {exc}") from exc


def render_season_report_html(report: SeasonReport) -> str:
    season = report.season
    comparison_name = (
        report.comparison_season.name if report.comparison_season is not None else "比較なし"
    )
    sample_notice = (
        f'<p class="notice">注意: {report.sample_threshold}戦未満のため少数標本です。</p>'
        if report.small_sample
        else ""
    )
    comparison_metric = report.comparison.comparison
    comparison_text = (
        "データなし"
        if comparison_metric is None
        else f"{_metric_text(comparison_metric)} / 勝率 {_rate(comparison_metric)}"
    )
    delta_text = (
        "算出不可"
        if report.comparison.win_rate_delta is None
        else f"{report.comparison.win_rate_delta * 100:+.1f}ポイント"
    )
    deck_rows = "".join(
        "<tr>"
        f'<td><span class="swatch" style="background:{escape(item.color or "#808080")}"></span>'
        f"{escape(item.deck_name)}</td>"
        f"<td>{escape(item.label)}</td>"
        f"<td>{item.metric.matches}</td><td>{item.metric.wins}</td>"
        f"<td>{item.metric.losses}</td><td>{item.metric.draws}</td>"
        f"<td>{_rate(item.metric)}</td>"
        f"<td>{'少数標本' if item.small_sample else ''}</td>"
        "</tr>"
        for item in report.deck_orders
    )
    axis_rows = "".join(
        "<tr>"
        f"<td>{escape(item.label)}</td><td>{item.metric.matches}</td>"
        f"<td>{item.metric.wins}</td><td>{item.metric.losses}</td>"
        f"<td>{item.metric.draws}</td><td>{_rate(item.metric)}</td>"
        f"<td>{'少数標本' if item.small_sample else ''}</td>"
        "</tr>"
        for item in report.axes
    )
    daily_rows = _trend_rows(report.daily_trend)
    weekly_rows = _trend_rows(report.weekly_trend)
    usage_rows = "".join(
        "<tr>"
        f"<td>{escape(point.label)}</td><td>{point.total_matches}</td>"
        f"<td>{escape(' / '.join(f'{item.deck_name} {item.matches}戦 ({item.ratio * 100:.1f}%)' for item in point.decks) or '対戦なし')}</td>"
        "</tr>"
        for point in report.weekly_deck_usage
    )
    generated = _local_time(report.generated_at)
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(season.name)} シーズンレポート</title>
<style>
:root {{ color-scheme: light; font-family: "Segoe UI", "Yu Gothic UI", sans-serif; color: #1c2423; background: #fff; }}
body {{ margin: 0 auto; max-width: 1080px; padding: 28px; line-height: 1.55; }}
h1 {{ color: #006a6a; margin-bottom: 4px; }} h2 {{ border-bottom: 2px solid #c6d7d5; padding-bottom: 5px; margin-top: 28px; }}
.meta {{ color: #52605e; }} .summary {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
.metric {{ border-left: 4px solid #006a6a; padding: 8px 12px; background: #f4f8f7; }}
.notice {{ color: #7a4d00; background: #fff2cc; padding: 8px 12px; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0 20px; }} th, td {{ border: 1px solid #bcc8c6; padding: 6px 8px; text-align: left; }}
th {{ background: #edf3f2; }} .swatch {{ display: inline-block; width: 5px; height: 1.1em; margin-right: 7px; vertical-align: text-bottom; }}
.memo {{ white-space: pre-wrap; border-left: 3px solid #8aa6a2; padding-left: 12px; min-height: 1em; }}
@media print {{ body {{ max-width: none; padding: 0; }} h2, table {{ break-inside: avoid; }} }}
</style>
</head>
<body>
<header><h1>{escape(season.name)}</h1><p class="meta">{season.start_date} - {season.end_date} / 生成日時 {escape(generated)}</p></header>
{sample_notice}
<section><h2>概要</h2><div class="summary">
<div class="metric"><strong>対象シーズン</strong><br>{_metric_text(report.comparison.current)}<br>勝率 {_rate(report.comparison.current)}</div>
<div class="metric"><strong>比較: {escape(comparison_name)}</strong><br>{comparison_text}</div>
<div class="metric"><strong>勝率差</strong><br>{escape(delta_text)}</div>
</div><p>母集団: 確定済みで勝敗入力済みの正常録画または手動戦績。シーズン割当と期間の両方が一致し、非表示の自分デッキは除外。</p></section>
<section><h2>デッキ・先後</h2><table><thead><tr><th>デッキ</th><th>区分</th><th>対戦</th><th>勝</th><th>負</th><th>引分</th><th>勝率</th><th>注意</th></tr></thead><tbody>{deck_rows}</tbody></table></section>
<section><h2>コイントス・勝敗内訳</h2><table><thead><tr><th>軸</th><th>対戦</th><th>勝</th><th>負</th><th>引分</th><th>勝率</th><th>注意</th></tr></thead><tbody>{axis_rows}</tbody></table></section>
<section><h2>日別推移</h2>{_trend_table(daily_rows)}</section>
<section><h2>週別推移</h2>{_trend_table(weekly_rows)}</section>
<section><h2>週別使用デッキ</h2><table><thead><tr><th>期間</th><th>対戦</th><th>使用比率</th></tr></thead><tbody>{usage_rows}</tbody></table></section>
<section><h2>振り返り</h2>
<h3>目標</h3><div class="memo">{_multiline(season.report_goal)}</div>
<h3>良かった点</h3><div class="memo">{_multiline(season.report_highlights)}</div>
<h3>課題</h3><div class="memo">{_multiline(season.report_challenges)}</div>
<h3>次期方針</h3><div class="memo">{_multiline(season.report_next_plan)}</div>
<h3>従来メモ</h3><div class="memo">{_multiline(season.report_notes)}</div>
</section>
</body>
</html>
"""


def _trend_rows(points: tuple[StatisticsTrendPoint, ...]) -> str:
    return "".join(
        f"<tr><td>{escape(point.label)}</td><td>{point.metric.matches}</td>"
        f"<td>{point.metric.wins}</td><td>{point.metric.losses}</td>"
        f"<td>{point.metric.draws}</td><td>{_rate(point.metric)}</td></tr>"
        for point in points
    )


def _trend_table(rows: str) -> str:
    return (
        "<table><thead><tr><th>期間</th><th>対戦</th><th>勝</th><th>負</th>"
        f"<th>引分</th><th>勝率</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _metric_text(metric: StatisticsMetric) -> str:
    return f"{metric.matches}戦 {metric.wins}勝 {metric.losses}敗 {metric.draws}引分"


def _rate(metric: StatisticsMetric) -> str:
    return "-" if metric.win_rate is None else f"{metric.win_rate * 100:.1f}%"


def _multiline(value: str) -> str:
    return escape(value or "未入力").replace("\n", "<br>")


def _local_time(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _validate_destination(path: Path) -> None:
    if path.suffix.casefold() != ".html":
        raise SeasonReportExportError("出力先は.htmlファイルで指定してください")
    if not path.stem or path.name in {".", ".."}:
        raise SeasonReportExportError("出力ファイル名が不正です")
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', path.name):
        raise SeasonReportExportError("出力ファイル名に使用できない文字があります")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{number}"
        for prefix in ("COM", "LPT")
        for number in range(1, 10)
    }
    if path.stem.rstrip(" .").upper() in reserved or path.name != path.name.rstrip(" ."):
        raise SeasonReportExportError("Windowsで使用できない出力ファイル名です")
