"""動画を必要としない対戦カード情報の編集画面。"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from .card_analysis_models import normalize_card_name
from .pyside_card_analysis import ACTIONS, ACTORS, STATUSES, _combo, _discard, _error, _named, _put_row, _selected_id, _table


INFORMATION_STATES = {"used": "使用を確認", "not_used": "未使用を確認", "unknown": "不明・確認解除"}
MIGRATION_REASONS = {
    "confirmed_observation_without_usage": "カード表示の確認だけで、使用したか判断できません",
    "unmapped_card": "カードを特定できません",
    "unknown_actor": "使用者を確認してください",
    "usage_nonuse_conflict": "使用と未使用の確認が食い違っています",
    "nonuse_clear_with_usage": "未使用の確認解除と使用の記録を照合してください",
    "invalid_opener_reference": "初動カードを特定できません",
    "orphan_confirmation_history": "対戦に対応する確認履歴を確認してください",
}


def migration_review_text(items) -> str:
    paragraphs = []
    for index, item in enumerate(items, 1):
        details = [f"カード: {item.card_name}", f"使用者: {ACTORS.get(item.actor, '不明')}"]
        if item.source_kind == "observation" or item.action != "unknown":
            details.append(f"行動: {ACTIONS.get(item.action, '不明')}")
        if item.source_kind == "observation":
            details.append(f"元の観測: {STATUSES.get(item.original_status, '不明')}")
            if item.supports_use is not None:
                details.append("使用の根拠: " + ("あり" if item.supports_use else "なし"))
        if item.nonuse_confirmed is not None:
            details.append("未使用確認: " + ("あり" if item.nonuse_confirmed else "解除済み"))
        if item.opener_status is not None:
            details.append("初動確認: " + ("確認済み" if item.opener_status == "confirmed" else "不明"))
        reason = MIGRATION_REASONS.get(item.reason, "元の情報を確認してください")
        paragraphs.append(f"{index}. {reason}\n" + " / ".join(details))
    return "\n\n".join(paragraphs)


class CardInformationPanel(QWidget):
    def __init__(self, service, duel_id: str, *, on_changed: Callable[[], None] = lambda: None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("duel_cards_panel")
        self.service, self.duel_id, self.on_changed = service, duel_id, on_changed
        self.review = None
        self.identities = ()
        self.baseline = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = _named(QScrollArea(), "duel_cards_scroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        outer.addWidget(scroll)
        scroll.setWidget(content)
        hint = QLabel("対戦のカード情報は動画なしでも編集できます。動画やマーカーを削除しても、確認した使用・未使用・初動は残ります。未確認や候補なしは未使用を意味しません。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = _table("duel_cards_table", ("カード", "使用者", "確認状態", "初動"))
        self.table.setMinimumHeight(140)
        layout.addWidget(self.table)
        self.card = _combo("duel_card_name", {})
        self.card.setEditable(True)
        self.card.setInsertPolicy(self.card.InsertPolicy.NoInsert)
        self.card.lineEdit().setPlaceholderText("カードを選択、または名称を入力")
        self.actor = _combo("duel_card_actor", ACTORS)
        self.state = _combo("duel_card_state", INFORMATION_STATES)
        self.opener = _named(QCheckBox("このカードを初動として確認"), "duel_card_opener")
        self.opener.setToolTip("同じ対戦・使用者の使用を確認したカードを、初動として保存します。")
        form = QFormLayout()
        form.addRow("カード", self.card)
        form.addRow("使用者", self.actor)
        form.addRow("確認状態", self.state)
        form.addRow(self.opener)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.new_button = _named(QPushButton("新しいカード"), "duel_card_new")
        self.save_button = _named(QPushButton("カード情報を保存"), "duel_card_save")
        self.reload_button = _named(QPushButton("再読込"), "duel_card_reload")
        for button in (self.new_button, self.save_button, self.reload_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.summary = _named(QLabel(), "duel_cards_summary")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.migration_summary = _named(QLabel(), "duel_cards_migration_summary")
        self.migration_summary.setWordWrap(True)
        layout.addWidget(self.migration_summary)
        self.migration_details = _named(QPlainTextEdit(), "duel_cards_migration_review")
        self.migration_details.setReadOnly(True)
        self.migration_details.setMinimumHeight(100)
        self.migration_details.setMaximumHeight(180)
        layout.addWidget(self.migration_details)
        self.status = _named(QLabel(), "duel_cards_status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.table.itemSelectionChanged.connect(self.select)
        self.new_button.clicked.connect(self.new)
        self.save_button.clicked.connect(self.save)
        self.reload_button.clicked.connect(self.reload)
        self.actor.currentIndexChanged.connect(self._update_opener_enabled)
        self.state.currentIndexChanged.connect(self._update_opener_enabled)
        self.reload()

    def snapshot(self):
        return self.card.currentText(), self.actor.currentData(), self.state.currentData(), self.opener.isChecked()

    def request_leave(self) -> bool:
        return _discard(self, self.baseline is not None and self.snapshot() != self.baseline)

    def _update_opener_enabled(self, *_args) -> None:
        enabled = self.actor.currentData() in {"self", "opponent"} and self.state.currentData() == "used"
        self.opener.setEnabled(enabled)
        if not enabled:
            self.opener.setChecked(False)

    def _load(self, information=None) -> None:
        self.card.setCurrentIndex(self.card.findData(information.card_id) if information else -1)
        self.card.setEditText(information.card_name if information else "")
        self.actor.setCurrentIndex(self.actor.findData(information.actor if information else "self"))
        self.state.setCurrentIndex(self.state.findData(information.state if information else "unknown"))
        self.opener.setChecked(bool(information and self.review and any(
            item.actor == information.actor and item.card_id == information.card_id and item.status == "confirmed"
            for item in self.review.openers
        )))
        self._update_opener_enabled()
        self.baseline = self.snapshot()

    def new(self) -> None:
        if self.request_leave():
            self.table.clearSelection()
            self._load()

    def select(self) -> None:
        selected = next((item for item in self.review.information if item.information_id == _selected_id(self.table)), None) if self.review else None
        if selected is None:
            return
        if self.request_leave():
            self._load(selected)
        else:
            self.table.blockSignals(True)
            self.table.clearSelection()
            self.table.blockSignals(False)

    def reload(self, *, force: bool = False) -> None:
        if not force and not self.request_leave():
            return
        try:
            review = self.service.get_duel_card_review(self.duel_id)
            identities = self.service.list_card_identities()
        except Exception as exc:
            self.status.setText(f"対戦カード情報を読み込めません: {exc}")
            return
        selected = _selected_id(self.table)
        self.review, self.identities = review, identities
        self.card.blockSignals(True)
        self.card.clear()
        for identity in identities:
            self.card.addItem(identity.name, identity.card_id)
        self.card.blockSignals(False)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for item in review.information:
            opener = any(value.actor == item.actor and value.card_id == item.card_id and value.status == "confirmed" for value in review.openers)
            _put_row(self.table, (item.card_name, ACTORS[item.actor], INFORMATION_STATES[item.state], "初動" if opener else ""), item.information_id)
        self.table.blockSignals(False)
        self._load(next((item for item in review.information if item.information_id == selected), None))
        self.summary.setText("初動確認: " + ("、".join(f"{ACTORS[item.actor]}:{item.card_name}" for item in review.openers if item.status == "confirmed") or "なし（未確認）"))
        reviews = getattr(review, "migration_reviews", ())
        self.migration_summary.setVisible(bool(reviews))
        self.migration_details.setVisible(bool(reviews))
        self.migration_summary.setText(
            f"移行時の要確認情報 {len(reviews)}件。元の情報を確認し、判断できる内容を上のカード情報へ手動登録・訂正してください。"
        )
        self.migration_details.setPlainText(migration_review_text(reviews))
        self.status.setText(f"対戦カード情報 {len(review.information)}件")

    def save(self) -> None:
        if self.review is None:
            return
        try:
            name = self.card.currentText().strip()
            identity = next((item for item in self.identities if normalize_card_name(item.name) == normalize_card_name(name)), None)
            if identity is None:
                identity = self.service.save_card_identity(name=name)
            actor = self.actor.currentData()
            previous = next((item for item in self.review.information if item.card_id == identity.card_id and item.actor == actor), None)
            opener = next((item for item in self.review.openers if item.actor == actor), None)
            result = self.service.save_duel_card_information(
                self.duel_id, identity.card_id, actor, self.state.currentData(),
                expected_revision=previous.revision if previous else 0,
                is_opener=self.opener.isChecked(), opener_expected_revision=opener.revision if opener else 0,
            )
        except Exception as exc:
            _error(self, "対戦カード情報を保存できません", exc)
            return
        self.reload(force=True)
        self._load(result)
        self.status.setText("対戦カード情報を保存しました。動画の時刻・マーカーは変更されません。")
        self.on_changed()
