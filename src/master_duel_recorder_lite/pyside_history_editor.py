"""戦績管理の下部編集。Qt依存は通常GUI起動時だけ読み込む。"""

from .duel_records import DuelRecordValues, duel_choice_label


def create_history_editor(parent, service):
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import (
        QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
        QLineEdit, QMessageBox, QPushButton, QSizePolicy, QTextEdit, QVBoxLayout,
        QWidget,
    )

    class HistoryEditor(QFrame):
        dirtyChanged = Signal()

        def __init__(self):
            super().__init__(parent)
            self.view = None
            self.record = None
            self.baseline = None
            self.loading = False
            self.setObjectName("history_editor")
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 10, 0, 0)
            layout.setSpacing(8)
            heading = QHBoxLayout()
            self.title = QLabel("選択中の戦績")
            self.title.setStyleSheet("font-size: 16px; font-weight: 600")
            heading.addWidget(self.title, 1)
            self.status = QLabel()
            heading.addWidget(self.status)
            layout.addLayout(heading)
            self.fields = {}
            self.groups = {}
            common = QGridLayout()
            common.setContentsMargins(0, 0, 0, 0)
            common.setColumnStretch(0, 2)
            common.setColumnStretch(1, 2)
            common.setColumnStretch(2, 2)
            common.addWidget(self.deck_field("自分デッキ", "own_deck"), 0, 0)
            common.addWidget(self.segment("勝敗", "result", ("unknown", "win", "loss", "draw")), 0, 1)
            common.addWidget(self.segment("先後", "play_order", ("unknown", "first", "second")), 0, 2)
            common.addWidget(self.segment("確認状態", "status", ("draft", "confirmed")), 1, 0)
            common.addWidget(self.segment("コイン", "coin_face", ("unknown", "heads", "tails")), 1, 1)
            self.details_toggle = QPushButton("詳細項目 ▸")
            self.details_toggle.setCheckable(True)
            common.addWidget(self.details_toggle, 1, 2)
            layout.addLayout(common)
            self.details = QWidget()
            detail = QGridLayout(self.details)
            detail.setContentsMargins(0, 0, 0, 0)
            detail.setColumnStretch(0, 1)
            detail.setColumnStretch(1, 1)
            detail.addWidget(self.deck_field("相手デッキ", "opponent_deck"), 0, 0)
            box = QWidget()
            row = QVBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QLabel("対戦種別 / シーズン"))
            pair = QHBoxLayout()
            kind = QComboBox()
            for value in ("other", "ranked", "event", "room", "solo"):
                kind.addItem(duel_choice_label("duel_type", value), value)
            self.fields["duel_type"] = kind
            season = QComboBox()
            self.fields["season_id"] = season
            pair.addWidget(kind)
            pair.addWidget(season, 1)
            row.addLayout(pair)
            detail.addWidget(box, 0, 1)
            tags = QLineEdit()
            tags.setPlaceholderText("タグ（カンマ区切り）")
            tags.setAccessibleName("タグ")
            self.fields["tags"] = tags
            detail.addWidget(tags, 1, 0, 1, 2)
            notes = QTextEdit()
            notes.setPlaceholderText("メモ")
            notes.setAccessibleName("メモ")
            notes.setFixedHeight(70)
            self.fields["notes"] = notes
            detail.addWidget(notes, 2, 0, 1, 2)
            layout.addWidget(self.details)
            self.details.hide()
            self.details_toggle.toggled.connect(self.toggle_details)
            for key, widget in self.fields.items():
                widget.setObjectName("history_edit_" + key)
                if isinstance(widget, QComboBox):
                    widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                    widget.setMinimumContentsLength(4)
                    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                    widget.currentTextChanged.connect(self.changed)
                else:
                    widget.textChanged.connect(self.changed)

        def deck_field(self, label, key):
            box = QWidget()
            layout = QVBoxLayout(box)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            layout.addWidget(QLabel(label))
            combo = QComboBox()
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.setToolTip("候補から選択、または自由入力")
            self.fields[key] = combo
            layout.addWidget(combo)
            return box

        def segment(self, label, field, choices):
            box = QWidget()
            layout = QVBoxLayout(box)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            layout.addWidget(QLabel(label))
            row = QHBoxLayout()
            row.setSpacing(0)
            group = QButtonGroup(self)
            self.groups[field] = group
            for choice in choices:
                button = QPushButton(duel_choice_label(field, choice))
                button.setObjectName("history_edit_" + field + "_" + choice)
                button.setCheckable(True)
                button.setProperty("segmentButton", True)
                button.setProperty("choiceData", choice)
                button.setStyleSheet("padding: 6px 5px; min-height: 20px;")
                group.addButton(button)
                row.addWidget(button, 1)
                button.toggled.connect(self.changed)
            layout.addLayout(row)
            return box

        def toggle_details(self, checked):
            self.details.setVisible(checked)
            self.details_toggle.setText("詳細項目 ▾" if checked else "詳細項目 ▸")

        def values(self):
            selected = {key: str(group.checkedButton().property("choiceData"))
                        for key, group in self.groups.items()}
            return DuelRecordValues(
                **selected,
                own_deck=self.fields["own_deck"].currentText(),
                opponent_deck=self.fields["opponent_deck"].currentText(),
                duel_type=self.fields["duel_type"].currentData(),
                season_id=self.fields["season_id"].currentData(),
                tags=tuple(v.strip() for v in self.fields["tags"].text().replace("、", ",").split(",") if v.strip()),
                notes=self.fields["notes"].toPlainText(),
            )

        def is_dirty(self):
            return self.baseline is not None and self.values() != self.baseline

        def changed(self, *_args):
            if not self.loading:
                self.status.setText("未保存の変更" if self.is_dirty() else "")
                self.dirtyChanged.emit()

        def bind(self, view):
            self.loading = True
            try:
                if view is None:
                    self.view = self.record = self.baseline = None
                    self.hide()
                    return
                data = service.get_duel_editor_data(view.recording_id)
                self.view = view
                self.record = view.duel_record or data.record
                values = self.record.values if self.record else data.values
                self.title.setText("選択中の戦績  " + view.occurred_at.astimezone().strftime("%Y/%m/%d %H:%M"))
                for key, group in self.groups.items():
                    for button in group.buttons():
                        button.setChecked(button.property("choiceData") == getattr(values, key))
                for key in ("own_deck", "opponent_deck"):
                    self.fields[key].clear()
                    self.fields[key].addItems([deck.name for deck in data.decks])
                    self.fields[key].setCurrentText(getattr(values, key))
                self.fields["duel_type"].setCurrentIndex(self.fields["duel_type"].findData(values.duel_type))
                season = self.fields["season_id"]
                season.clear()
                season.addItem("シーズン未設定", None)
                for entry in data.seasons:
                    season.addItem(entry.name, entry.season_id)
                if values.season_id is not None and season.findData(values.season_id) < 0:
                    season.addItem(f"シーズン {values.season_id}", values.season_id)
                season.setCurrentIndex(max(0, season.findData(values.season_id)))
                self.fields["tags"].setText(", ".join(values.tags))
                self.fields["notes"].setPlainText(values.notes)
                self.baseline = self.values()
                reason = service.duel_write_block_reason()
                self.setEnabled(reason is None)
                self.status.setText(reason or "")
                self.show()
            finally:
                self.loading = False

        def save(self):
            if self.view is None:
                return False
            try:
                reason = service.duel_write_block_reason()
                if reason:
                    raise ValueError(reason)
                values = self.values()
                if self.record:
                    record = service.update_duel_record(self.record.duel_id, values, expected_revision=self.record.revision)
                else:
                    record = service.save_duel_record(self.view.recording_id, values, expected_revision=0)
            except Exception as exc:
                self.status.setText("保存できません。入力内容を保持しています")
                QMessageBox.warning(self, "戦績を保存できません", str(exc))
                return False
            self.record = record
            self.baseline = values
            self.status.setText("保存しました")
            self.dirtyChanged.emit()
            return True

    return HistoryEditor()
