"""使用カード分析の対象管理、レビュー、集計のQt操作面。"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)


ACTORS = {"self": "自分", "opponent": "相手", "unknown": "不明"}
ACTIONS = {"activate": "発動", "normal_summon": "通常召喚", "special_summon": "特殊召喚", "unknown": "不明"}
STATUSES = {"candidate": "候補", "confirmed": "確認済み", "rejected": "却下"}
JOB_STATUSES = {"queued": "解析待ち", "running": "解析中", "completed": "解析完了",
                "failed": "解析失敗", "waiting": "中断・再開待ち", "cancelled": "中断・再開待ち"}


def _named(widget, name: str):
    widget.setObjectName(name)
    return widget


def _combo(name: str, choices: dict[str, str]) -> QComboBox:
    combo = _named(QComboBox(), name)
    for value, label in choices.items():
        combo.addItem(label, value)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(6)
    combo.setMinimumWidth(0)
    return combo


def _table(name: str, headers: tuple[str, ...]) -> QTableWidget:
    table = _named(QTableWidget(0, len(headers)), name)
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.setMinimumWidth(0)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    return table


def _put_row(table: QTableWidget, values, identity=None, tooltip: str = "") -> None:
    row = table.rowCount()
    table.insertRow(row)
    for column, value in enumerate(values):
        item = QTableWidgetItem(str(value))
        item.setToolTip(tooltip or str(value))
        item.setData(Qt.ItemDataRole.UserRole, identity)
        table.setItem(row, column, item)


def _selected_id(table: QTableWidget):
    item = table.item(table.currentRow(), 0)
    return item.data(Qt.ItemDataRole.UserRole) if item is not None else None


def _discard(parent: QWidget, dirty: bool) -> bool:
    return not dirty or QMessageBox.question(
        parent, "未保存の変更", "編集中の変更を破棄しますか？",
        QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    ) == QMessageBox.StandardButton.Discard


def _error(parent: QWidget, title: str, error: Exception) -> None:
    QMessageBox.warning(parent, title, str(error) + "\n変更は保存されていません。必要なら再読込してください。")


def _fill_targets(combo: QComboBox, targets, *, optional: bool = False) -> None:
    selected = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    if optional:
        combo.addItem("すべての初動", None)
    for target in targets:
        combo.addItem(target.name + ("（無効）" if not getattr(target, "enabled", True) else ""),
                      getattr(target, "target_id", None) or target.card_id)
        combo.setItemData(combo.count() - 1, target.name, Qt.ItemDataRole.ToolTipRole)
    index = combo.findData(selected)
    if index >= 0:
        combo.setCurrentIndex(index)
    combo.blockSignals(False)


def _time(milliseconds: int | None) -> str:
    if milliseconds is None:
        return "未確認"
    seconds = milliseconds / 1000
    return f"{int(seconds // 60):02d}:{seconds % 60:06.3f}"


def _evidence_text(evidence: dict[str, object]) -> str:
    kind = {
        "log_use": "ログの使用表示",
        "log_unclassified": "カード名の表示（行動の読取なし）",
        "detail_display": "カード詳細の表示",
    }.get(evidence.get("kind"), "観測位置の動画で確認してください。")
    texts = [str(evidence[key]) for key in ("name_text", "action_text", "reason") if evidence.get(key)]
    return (kind + ("\n読取表示: " + " / ".join(texts) if texts else "")
            + ("\n使用は利用者が確認済みです。" if evidence.get("manually_confirmed_use") else ""))


class CardTargetPanel(QWidget):
    def __init__(self, service, parent=None, *, autoload: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("card_targets_panel")
        self.service = service
        self.targets = ()
        self.current = None
        self.baseline = None
        layout = QVBoxLayout(self)
        hint = QLabel("分析したいカードを選びます。別名は読取時の表記、用途は分類のためのメモです。無効化しても保存済みの結果は保持します。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = _table("card_targets_table", ("カード", "用途", "有効"))
        self.table.setMinimumHeight(120)
        layout.addWidget(self.table, 1)
        self.name = _named(QLineEdit(), "card_target_name")
        self.aliases = _named(QTextEdit(), "card_target_aliases")
        self.aliases.setMaximumHeight(75)
        self.aliases.setPlaceholderText("別名を1行に1つ")
        self.purpose = _named(QLineEdit(), "card_target_purpose")
        self.enabled = _named(QCheckBox("解析対象にする"), "card_target_enabled")
        self.enabled.setChecked(True)
        form = QFormLayout()
        form.addRow("名称", self.name)
        form.addRow("別名", self.aliases)
        form.addRow("用途", self.purpose)
        form.addRow(self.enabled)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        for name, label, action in (("card_target_new", "新規", self.new),
                                    ("card_target_save", "対象を保存", self.save),
                                    ("card_target_reload", "再読込", self.reload)):
            button = _named(QPushButton(label), name)
            button.clicked.connect(action)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status = _named(QLabel(), "card_target_status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table.itemSelectionChanged.connect(self.select)
        self.load_target(None)
        if autoload:
            self.reload()

    def snapshot(self):
        return self.name.text(), self.aliases.toPlainText(), self.purpose.text(), self.enabled.isChecked()

    def request_leave(self) -> bool:
        return _discard(self, self.baseline is not None and self.snapshot() != self.baseline)

    def load_target(self, target) -> None:
        self.current = target
        self.name.setText(target.name if target else "")
        self.aliases.setPlainText("\n".join(target.aliases) if target else "")
        self.purpose.setText(target.purpose if target else "")
        self.enabled.setChecked(target.enabled if target else True)
        self.baseline = self.snapshot()

    def new(self) -> None:
        if self.request_leave():
            self.table.clearSelection()
            self.load_target(None)
            self.status.setText("新しい対象カードを入力してください。")

    def select(self) -> None:
        selected = next((target for target in self.targets if target.target_id == _selected_id(self.table)), None)
        if selected is not None and selected != self.current:
            if not self.request_leave():
                self.table.blockSignals(True)
                for row, target in enumerate(self.targets):
                    if self.current is not None and target.target_id == self.current.target_id:
                        self.table.selectRow(row)
                self.table.blockSignals(False)
                return
            self.load_target(selected)

    def reload(self) -> None:
        if not self.request_leave():
            return
        try:
            self.targets = self.service.list_card_targets(include_disabled=True)
        except Exception as exc:
            _error(self, "対象カードを読み込めません", exc)
            return
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for target in self.targets:
            _put_row(self.table, (target.name, target.purpose, "有効" if target.enabled else "無効"), target.target_id,
                     target.name + "\n別名: " + "、".join(target.aliases) + "\n用途: " + target.purpose)
            if self.current is not None and target.target_id == self.current.target_id:
                self.table.selectRow(self.table.rowCount() - 1)
        self.table.blockSignals(False)
        current = next((target for target in self.targets if self.current and target.target_id == self.current.target_id), None)
        self.load_target(current)
        self.status.setText(f"対象 {len(self.targets)}件（有効 {sum(target.enabled for target in self.targets)}件）")

    def save(self) -> None:
        try:
            target = self.service.save_card_target(
                name=self.name.text().strip(), aliases=tuple(line.strip() for line in self.aliases.toPlainText().splitlines() if line.strip()),
                purpose=self.purpose.text().strip(), enabled=self.enabled.isChecked(),
                target_id=self.current.target_id if self.current else None,
                expected_revision=self.current.revision if self.current else None,
            )
        except Exception as exc:
            _error(self, "対象カードを保存できません", exc)
            return
        self.load_target(target)
        self.reload()
        self.status.setText("対象カードを保存しました。過去の解析条件は変更されません。")


class CardReviewPanel(QWidget):
    def __init__(self, service, recording_id: str, *, seek: Callable[[int], None], on_changed: Callable[[], None], duration_seconds=0, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("review_cards_tab")
        self.service, self.recording_id, self.seek, self.on_changed = service, recording_id, seek, on_changed
        self.review = None
        self.current = None
        self.baseline = None
        self.targets = ()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = _named(QScrollArea(), "review_cards_scroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        outer.addWidget(scroll)
        scroll.setWidget(content)
        self.status = _named(QLabel(), "review_cards_status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        operations = QGridLayout()
        self.analyze = _named(QPushButton("解析・再解析"), "review_cards_analyze")
        self.refresh_button = _named(QPushButton("再読込"), "review_cards_reload")
        operations.addWidget(self.analyze, 0, 0)
        operations.addWidget(self.refresh_button, 0, 1)
        layout.addLayout(operations)
        hint = QLabel("候補は動画で確認してください。「使用を確定」は対戦のカード情報へ保存します。時刻の訂正や候補の却下では、保存した使用情報・初動は変わりません。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = _table("review_cards_table", ("観測位置", "カード", "状態"))
        self.table.setColumnWidth(0, 82)
        self.table.setColumnWidth(1, 160)
        self.table.setMinimumHeight(140)
        layout.addWidget(self.table)
        self.name = _combo("review_card_name", {})
        self.actor = _combo("review_card_actor", ACTORS)
        self.action = _combo("review_card_action", ACTIONS)
        self.actual_known = _named(QCheckBox("実使用時刻を確認した"), "review_card_actual_known")
        self.actual_seconds = _named(QDoubleSpinBox(), "review_card_actual_seconds")
        self.actual_seconds.setDecimals(3)
        self.actual_seconds.setSuffix(" 秒")
        self.actual_seconds.setRange(0, duration_seconds if duration_seconds is not None else 99_999_999)
        self.actual_seconds.setEnabled(False)
        self.opener = _named(QCheckBox("この使用を初動として確認"), "review_card_opener")
        self.opener.setToolTip("動画で使用と初動を確認し、使用者と行動を選んで「使用を確定」で保存します。")
        self.observed = _named(QLabel("行を選択してください"), "review_card_observed_time")
        self.observed.setWordWrap(True)
        self.evidence = _named(QLabel(), "review_card_evidence")
        self.evidence.setWordWrap(True)
        self.evidence.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        form = QFormLayout()
        for label, widget in (("名称", self.name), ("使用者", self.actor), ("行動", self.action), ("観測位置", self.observed)):
            form.addRow(label, widget)
        form.addRow(self.actual_known)
        form.addRow("実使用時刻", self.actual_seconds)
        form.addRow(self.opener)
        layout.addLayout(form)
        layout.addWidget(self.evidence)
        actions = QGridLayout()
        self.edit_buttons = []
        for index, (name, label, callback) in enumerate((
            ("review_card_seek", "観測位置へ移動", self.seek_current),
            ("review_card_save", "訂正を保存", lambda: self.save(None)),
            ("review_card_confirm", "使用を確定", lambda: self.save("confirmed")),
            ("review_card_reject", "候補を却下", lambda: self.save("rejected")),
        )):
            button = _named(QPushButton(label), name)
            button.clicked.connect(callback)
            actions.addWidget(button, index // 2, index % 2)
            self.edit_buttons.append(button)
        layout.addLayout(actions)
        self.confirm_target = _combo("review_card_nonuse_target", {})
        self.confirm_actor = _combo("review_card_nonuse_actor", {key: label for key, label in ACTORS.items() if key != "unknown"})
        form = QFormLayout()
        form.addRow("非使用の対象", self.confirm_target)
        form.addRow("確認する使用者", self.confirm_actor)
        layout.addLayout(form)
        confirmations = QGridLayout()
        for index, (name, label, callback) in enumerate((
            ("review_card_nonuse_confirm", "未使用を確認", lambda: self.set_nonuse(True)),
            ("review_card_nonuse_clear", "未使用確認を解除", lambda: self.set_nonuse(False)),
            ("review_card_opener_clear", "初動確認を解除", self.clear_opener),
        )):
            button = _named(QPushButton(label), name)
            button.clicked.connect(callback)
            confirmations.addWidget(button, index // 2, index % 2)
        layout.addLayout(confirmations)
        self.confirmation_summary = _named(QLabel(), "review_card_confirmation_summary")
        self.confirmation_summary.setWordWrap(True)
        layout.addWidget(self.confirmation_summary)
        self.table.itemSelectionChanged.connect(self.select)
        self.table.cellDoubleClicked.connect(lambda *_args: self.seek_current())
        self.actual_known.toggled.connect(self.actual_seconds.setEnabled)
        self.analyze.clicked.connect(self.request_analysis)
        self.refresh_button.clicked.connect(self.reload)
        self.reload()
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def snapshot(self):
        return (self.name.currentData(), self.actor.currentData(), self.action.currentData(),
                self.actual_known.isChecked(), self.actual_seconds.value(), self.opener.isChecked())

    def dirty(self) -> bool:
        return self.baseline is not None and self.snapshot() != self.baseline

    def request_leave(self) -> bool:
        return _discard(self, self.dirty())

    def load_observation(self, observation) -> None:
        self.current = observation
        for button in self.edit_buttons:
            button.setEnabled(observation is not None and getattr(observation, "video_verified", False))
        for widget in (self.name, self.actor, self.action, self.actual_known, self.opener):
            widget.setEnabled(observation is not None)
        if observation is not None:
            values = observation.values
            self.name.setCurrentIndex(self.name.findData(values.target_id))
            self.actor.setCurrentIndex(self.actor.findData(values.actor))
            self.action.setCurrentIndex(self.action.findData(values.action))
            self.actual_known.setChecked(values.actual_ms is not None)
            self.actual_seconds.setValue((values.actual_ms or 0) / 1000)
            self.opener.setChecked(observation.is_opener)
            self.observed.setText(_time(values.observed_ms) + "（表示の観測位置）")
            evidence = _evidence_text(values.evidence)
            self.evidence.setText("根拠: " + evidence)
            self.evidence.setToolTip(evidence)
        else:
            self.observed.setText("行を選択してください")
            self.evidence.clear()
            self.opener.setChecked(False)
            self.actual_known.setChecked(False)
            self.actual_seconds.setValue(0)
        self.actual_seconds.setEnabled(observation is not None and self.actual_known.isChecked())
        self.baseline = self.snapshot()

    def select(self) -> None:
        selected = next((observation for observation in self.review.observations if observation.event_id == _selected_id(self.table)), None) if self.review else None
        if selected is not None and selected != self.current:
            if not self.request_leave():
                self.table.blockSignals(True)
                for row, observation in enumerate(self.review.observations):
                    if self.current is not None and observation.event_id == self.current.event_id:
                        self.table.selectRow(row)
                self.table.blockSignals(False)
                return
            self.load_observation(selected)

    def poll(self) -> None:
        if not self.isVisible():
            return
        if self.dirty():
            try:
                review = self.service.get_card_analysis_review(self.recording_id)
                enabled = self.service.load_config().config.card_analysis_enabled
            except Exception as exc:
                self.status.setText(f"解析状態を確認できません: {exc}（編集中の入力を保持しています）")
                return
            self.show_status(review, enabled, holding_edits=True)
        else:
            self.reload(force=True)

    def show_status(self, review, enabled: bool, *, holding_edits=False) -> None:
        job = review.job
        status = JOB_STATUSES.get(job.state, job.state) if job else "未解析"
        if job and job.state == "running":
            status += f" {job.progress * 100:.0f}%"
        details = getattr(job, "error", None) or getattr(job, "message", None) or ""
        self.status.setText(f"{status} / 保存済み {len(review.observations)}件" + (f"\n{details}" if details else "")
                           + ("\n前回の成功結果を表示しています。" if job and job.state != "completed" and review.successful_job else "")
                           + ("\n設定の「使用カード分析」で有効な解析対象を登録してください。" if not any(target.enabled for target in self.targets) else "")
                           + ("\n解析はOFFです。設定の「使用カード分析」で有効にできます。" if not enabled else "")
                           + ("\n編集中の入力を保持しています。" if holding_edits else ""))
        self.analyze.setEnabled(enabled and (not job or job.state not in {"queued", "running"}) and any(target.enabled for target in self.targets))

    def reload(self, *, force=False) -> None:
        if not force and not self.request_leave():
            return
        try:
            review = self.service.get_card_analysis_review(self.recording_id)
            targets = self.service.list_card_targets(include_disabled=True)
            enabled = self.service.load_config().config.card_analysis_enabled
        except Exception as exc:
            self.status.setText(f"解析情報を読み込めません: {exc}")
            return
        self.review, self.targets = review, targets
        _fill_targets(self.name, targets)
        _fill_targets(self.confirm_target, targets)
        self.show_status(review, enabled)
        previous_id = self.current.event_id if self.current else None
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for observation in review.observations:
            values = observation.values
            tooltip = (f"{values.card_name}\n{ACTORS.get(values.actor, '不明')} / {ACTIONS.get(values.action, '不明')}"
                       f"\n観測位置 {_time(values.observed_ms)} / 実使用時刻 {_time(values.actual_ms)}")
            _put_row(self.table, (_time(values.observed_ms), values.card_name, STATUSES.get(observation.status, observation.status)), observation.event_id, tooltip)
            if observation.event_id == previous_id:
                self.table.selectRow(self.table.rowCount() - 1)
        self.table.blockSignals(False)
        self.load_observation(next((observation for observation in review.observations if observation.event_id == previous_id), None))
        nonuses = [f"{ACTORS.get(item.actor, '不明')}:{next((target.name for target in targets if target.target_id == item.target_id), '対象カード')}"
                   for item in review.nonuses if item.confirmed]
        openers = [f"{ACTORS.get(item.actor, '不明')}:{next((target.name for target in targets if target.target_id == item.target_id), '不明')}"
                   for item in review.openers if item.status == "confirmed"]
        self.confirmation_summary.setText("未使用確認: " + ("、".join(nonuses) or "なし（未確認）")
                                          + "\n初動確認: " + ("、".join(openers) or "なし（未確認）"))

    def seek_current(self) -> None:
        if self.current is not None and getattr(self.current, "video_verified", False):
            self.seek(self.current.values.observed_ms)

    def save(self, status: str | None) -> None:
        if self.current is None:
            return
        target = next((target for target in self.targets if target.target_id == self.name.currentData()), None)
        if target is None:
            QMessageBox.warning(self, "名称を選択してください", "対象管理で登録したカードを選択してください。")
            return
        selected_status = status or self.current.status
        if status == "confirmed" and (
            self.actor.currentData() == "unknown" or self.action.currentData() == "unknown"
        ):
            QMessageBox.warning(self, "使用者と行動の確認", "動画で使用を確認し、使用者と行動を選択してから確定してください。")
            return
        if self.opener.isChecked() and selected_status not in {"confirmed", "rejected"}:
            QMessageBox.warning(self, "初動確認", "初動は使用を確定するときに保存できます。「使用を確定」を選択してください。")
            return
        try:
            previous_opener = next((item for item in self.review.openers if item.actor == self.actor.currentData()), None)
            information = next((item for item in getattr(self.review, "information", ())
                                if item.card_id == getattr(target, "card_id", target.target_id)
                                and item.actor == self.actor.currentData()), None)
            updated = self.service.update_card_observation(
                self.current.event_id, expected_revision=self.current.revision,
                opener_expected_revision=previous_opener.revision if previous_opener else 0,
                information_expected_revision=information.revision if information else 0,
                expected_generation=self.current.generation,
                target_id=target.target_id, card_name=target.name,
                actor=self.actor.currentData(), action=self.action.currentData(),
                actual_ms=round(self.actual_seconds.value() * 1000) if self.actual_known.isChecked() else None,
                time_kind="actual_use" if self.actual_known.isChecked() else (
                    self.current.values.time_kind if self.current.values.time_kind != "actual_use" else "log_observed"
                ),
                status=status, is_opener=True if self.opener.isChecked() and status == "confirmed" else None,
            )
        except Exception as exc:
            _error(self, "カード使用を保存できません", exc)
            return
        self.load_observation(updated)
        self.reload(force=True)
        self.on_changed()

    def request_analysis(self) -> None:
        if not self.request_leave():
            return
        self.analyze.setEnabled(False)
        try:
            self.service.request_card_analysis(self.recording_id, reanalyse=True)
        except Exception as exc:
            _error(self, "解析を開始できません", exc)
        self.reload(force=True)

    def set_nonuse(self, confirmed: bool) -> None:
        if self.review is None:
            return
        target_id = self.confirm_target.currentData()
        if target_id is None:
            return
        if not self.request_leave():
            return
        actor = self.confirm_actor.currentData()
        previous = next((item for item in self.review.nonuses if item.target_id == target_id and item.actor == actor), None)
        try:
            self.service.set_card_nonuse(self.recording_id, target_id, actor, confirmed=confirmed,
                                         expected_revision=previous.revision if previous else 0)
        except Exception as exc:
            _error(self, "未使用確認を保存できません", exc)
            return
        self.reload(force=True)
        self.on_changed()

    def clear_opener(self) -> None:
        if self.review is None:
            return
        if not self.request_leave():
            return
        actor = self.confirm_actor.currentData()
        previous = next((item for item in self.review.openers if item.actor == actor), None)
        try:
            self.service.set_card_opener(self.recording_id, actor, status="unknown",
                                         expected_revision=previous.revision if previous else 0)
        except Exception as exc:
            _error(self, "初動確認を解除できません", exc)
            return
        self.reload(force=True)
        self.on_changed()


class CardStatisticsPanel(QWidget):
    def __init__(self, service, *, filters: Callable[[], object], parent=None, autoload: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("statistics_cards_panel")
        self.service, self.filters = service, filters
        layout = QVBoxLayout(self)
        self.target = _combo("statistics_card_target", {})
        self.actor = _combo("statistics_card_actor", {key: label for key, label in ACTORS.items() if key != "unknown"})
        self.opener = _combo("statistics_card_opener_filter", {})
        self.candidates = _named(QCheckBox("候補を含む参考比較"), "statistics_card_candidates")
        controls = QGridLayout()
        for row, (label, widget) in enumerate((("比較するカード", self.target), ("使用者", self.actor), ("初動の条件", self.opener))):
            controls.addWidget(QLabel(label), row, 0)
            controls.addWidget(widget, row, 1)
        controls.addWidget(self.candidates, 3, 0, 1, 2)
        controls.setColumnStretch(1, 1)
        layout.addLayout(controls)
        self.status = _named(QLabel(), "statistics_card_status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = _table("statistics_card_table", ("区分", "対戦", "勝", "負", "引分", "勝率"))
        self.table.setColumnWidth(0, 156)
        self.table.setToolTip("1対戦を1件で集計。未解析・対象外・候補だけ・解析失敗は、確認済み未使用へ含めません。")
        layout.addWidget(self.table, 1)
        layout.addWidget(QLabel("確認済み初動別の勝敗"))
        self.opener_table = _table("statistics_card_opener_table", ("初動カード", "対戦", "勝", "負", "引分", "勝率"))
        layout.addWidget(self.opener_table, 1)
        hint = QLabel("勝率は勝利数÷対戦数（引分を含む）。10戦未満は少数の参考値です。最初に検出されたカードを自動で初動とは扱いません。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        if autoload:
            self.reload_targets()
        for widget in (self.target, self.actor, self.opener):
            widget.currentIndexChanged.connect(self.refresh)
        self.candidates.toggled.connect(self.refresh)

    def reload_targets(self) -> None:
        try:
            identities = getattr(self.service, "list_card_identities", None)
            targets = identities() if callable(identities) else self.service.list_card_targets(include_disabled=True)
        except Exception as exc:
            self.status.setText(f"対象カードを読み込めません: {exc}")
            return
        _fill_targets(self.target, targets)
        _fill_targets(self.opener, targets, optional=True)

    @staticmethod
    def metric_row(label: str, metric):
        rate = "—" if metric.win_rate is None else f"{metric.win_rate * 100:.1f}%"
        return label, metric.matches, metric.wins, metric.losses, metric.draws, rate

    def refresh(self, *_args) -> None:
        if self.target.currentData() is None:
            self.status.setText("比較する対象カードを登録してください。")
            self.table.setRowCount(0)
            self.opener_table.setRowCount(0)
            return
        try:
            report = self.service.get_card_statistics(
                self.filters(), target_id=self.target.currentData(), actor=self.actor.currentData(),
                opener_target_id=self.opener.currentData(), include_candidates=self.candidates.isChecked(),
            )
        except Exception as exc:
            self.status.setText(f"集計できません: {exc}（前回の結果を表示中）")
            return
        self.table.setRowCount(0)
        mode = "候補を含む参考比較" if report.include_candidates else "確認済み比較"
        for label, metric in (("使用（候補含む）" if report.include_candidates else "確認済み使用", report.used),
                              ("確認済み未使用", report.not_used), ("不明・未確認", report.unknown)):
            _put_row(self.table, self.metric_row(label, metric))
        self.opener_table.setRowCount(0)
        for row in report.by_opener:
            _put_row(self.opener_table, self.metric_row(row.label, row.metric), row.target_id, row.label)
        self.status.setText(f"{mode} / 条件内 {report.total.matches}戦 / 不明 {report.unknown.matches}戦"
                            f"\n確認済み使用 {report.confirmed_used.matches}戦・候補のみ {report.candidate_used.matches}戦"
                            f"\n共通条件の母集団 {report.base_total.matches}戦 / 初動未確認 {report.unknown_opener_count}戦")
