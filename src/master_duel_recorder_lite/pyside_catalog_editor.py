"""デッキ名・タグ・シーズンの一覧と編集。DB操作は既存サービスへ委譲する。"""

from datetime import date


def create_catalog_page(owner, key):
    from PySide6.QtCore import QDate, QSignalBlocker, Qt
    from PySide6.QtGui import QColor, QIcon, QPixmap
    from PySide6.QtWidgets import (
        QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QFormLayout,
        QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
        QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    )

    class CatalogPage(QWidget):
        def __init__(self):
            super().__init__()
            self.key = key
            self.prefix = {"decks": "deck", "tags": "tag", "seasons": "season"}[key]
            self.noun = {"decks": "デッキ", "tags": "タグ", "seasons": "シーズン"}[key]
            self.entries = {}
            self.selected_id = None
            self.creating = False
            self.baseline = None
            self.filters = ("", 0)
            self.setObjectName("catalogManager")
            self.setStyleSheet("""
                QWidget#catalogManager { background: #fafcfc; }
                QWidget#catalogManager QLabel, QWidget#catalogManager QLineEdit,
                QWidget#catalogManager QComboBox, QWidget#catalogManager QCheckBox,
                QWidget#catalogManager QPushButton, QWidget#catalogManager QTableWidget,
                QWidget#catalogManager QDateEdit { font-size: 14px; }
                QLineEdit, QComboBox, QDateEdit { min-height: 28px; }
                QPushButton { min-height: 32px; padding: 4px 10px; }
                QPushButton[primary="true"] { background: #007f7b; color: white; }
                QTableWidget { background: white; border: 1px solid #d7e2e3;
                    selection-background-color: #d3eeee; selection-color: #163337; }
            """)
            root = QHBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            self.splitter = QSplitter(Qt.Orientation.Horizontal)
            self.splitter.setChildrenCollapsible(False)
            root.addWidget(self.splitter)
            left = QWidget()
            listing = QVBoxLayout(left)
            listing.setContentsMargins(0, 0, 12, 0)
            self.search = self.register("search", QLineEdit())
            self.search.setPlaceholderText(f"{self.noun}名・説明を検索")
            self.search.setClearButtonEnabled(True)
            listing.addWidget(self.search)
            tools = QHBoxLayout()
            self.filter = self.register("filter", QComboBox())
            self.filter.setMaximumWidth(220)
            self.filter.addItems({"decks": ("すべて", "通常", "相手専用", "非表示"),
                                  "tags": ("すべて", "通常", "デッキ専用"),
                                  "seasons": ("すべて", "有効", "アーカイブ")}[key])
            tools.addWidget(self.filter, 1)
            add = self.register("add", QPushButton(f"＋ 新規{self.noun}"))
            add.setMaximumWidth(180)
            add.clicked.connect(self.new)
            tools.addStretch(1)
            tools.addWidget(add)
            listing.addLayout(tools)
            self.summary = QLabel()
            listing.addWidget(self.summary)
            self.table = QTableWidget()
            table_key = {"decks": "deck_catalog_table", "tags": "tag_catalog_table", "seasons": "season_table"}[key]
            owner._register(table_key, self.table)
            headers = {"decks": ("色", "デッキ名", "説明", "使用回数", "用途", "履歴・統計"),
                       "tags": ("色", "タグ名", "説明", "用途"),
                       "seasons": ("シーズン", "種別", "期間", "状態")}[key]
            self.table.setColumnCount(len(headers))
            self.table.setHorizontalHeaderLabels(headers)
            self.table.verticalHeader().hide()
            self.table.verticalHeader().setDefaultSectionSize(48)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setShowGrid(False)
            self.table.setAlternatingRowColors(True)
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeader().setSectionResizeMode(0 if key == "seasons" else 1, QHeaderView.ResizeMode.Stretch)
            self.table.setMinimumWidth(240)
            listing.addWidget(self.table, 1)
            if key == "decks":
                owner._register("catalog_table", self.summary)
            self.splitter.addWidget(left)
            right = QWidget()
            right.setObjectName("catalogInspector")
            right.setStyleSheet("QWidget#catalogInspector { background: white; }")
            right.setMinimumWidth(310)
            editing = QVBoxLayout(right)
            editing.setContentsMargins(18, 8, 8, 8)
            self.title = QLabel()
            self.title.setStyleSheet("font-size: 20px; font-weight: 600;")
            self.title.setWordWrap(True)
            editing.addWidget(self.title)
            self.info = QLabel()
            self.info.setWordWrap(True)
            editing.addWidget(self.info)
            self.form_widget = owner._register(f"{self.prefix}_editor", QWidget())
            form = QFormLayout(self.form_widget)
            form.setContentsMargins(0, 16, 0, 16)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setVerticalSpacing(12)
            self.fields = {}
            for field, label in (("name_input", "名前"), ("description_input", "説明")):
                self.fields[field] = self.register(field, QLineEdit())
                form.addRow(label, self.fields[field])
            if key == "seasons":
                self.fields["type_select"] = self.register("type_select", QComboBox())
                self.fields["type_select"].addItems(("ランク戦", "イベント", "カスタム"))
                form.addRow("種別", self.fields["type_select"])
                for field, label in (("start_date_picker", "開始日"), ("end_date_picker", "終了日")):
                    widget = self.register(field, QDateEdit(QDate.currentDate()))
                    widget.setCalendarPopup(True)
                    widget.setDisplayFormat("yyyy-MM-dd")
                    self.fields[field] = widget
                    form.addRow(label, widget)
            else:
                self.color = self.register("color_button", QPushButton())
                self.color.setMaximumWidth(150)
                self.color.clicked.connect(lambda: owner._choose_catalog_color(key))
                form.addRow("識別カラー", self.color)
                for field, label in ({"opponent_only": "相手デッキのみで使用", "hidden_from_history": "履歴・統計で非表示"}
                                     if key == "decks" else {"deck_only": "デッキ名登録でのみ使用"}).items():
                    self.fields[field] = self.register(field, QCheckBox(label))
                    form.addRow(self.fields[field])
            editing.addWidget(self.form_widget)
            self.save_button = self.register("save", QPushButton("変更を保存"))
            self.save_button.setProperty("primary", True)
            self.save_button.clicked.connect(self.save)
            editing.addWidget(self.save_button)
            self.cancel = self.register("cancel", QPushButton("取消"))
            self.cancel.clicked.connect(self.discard)
            editing.addWidget(self.cancel)
            editing.addStretch(1)
            self.extra = QWidget()
            actions = QVBoxLayout(self.extra)
            actions.setContentsMargins(0, 16, 0, 0)
            if key == "seasons":
                report = self.register("report", QPushButton("レポートを開く"))
                report.clicked.connect(self.report)
                actions.addWidget(report)
            self.remove_button = self.register("archive" if key == "seasons" else "delete",
                                               QPushButton("アーカイブ" if key == "seasons" else f"この{self.noun}を削除"))
            self.remove_button.clicked.connect(self.remove)
            if key != "seasons":
                self.remove_button.setStyleSheet("color: #b42318; border: none; text-align: left;")
            actions.addWidget(self.remove_button)
            editing.addWidget(self.extra)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(right)
            scroll.setMinimumWidth(330)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            self.splitter.addWidget(scroll)
            self.splitter.setSizes([650, 370])
            self.table.itemSelectionChanged.connect(self.selection_changed)
            self.search.textChanged.connect(self.filter_changed)
            self.filter.currentIndexChanged.connect(self.filter_changed)
            self.fill(None)

        def register(self, suffix, widget):
            return owner._register(f"{self.prefix}_{suffix}", widget)

        def identifier(self, entry):
            return entry.season_id if key == "seasons" else entry.entry_id

        def snapshot(self):
            values = []
            for widget in self.fields.values():
                if isinstance(widget, QCheckBox):
                    values.append(widget.isChecked())
                elif isinstance(widget, QComboBox):
                    values.append(widget.currentText())
                elif isinstance(widget, QDateEdit):
                    values.append(widget.date().toString("yyyy-MM-dd"))
                else:
                    values.append(widget.text())
            if key != "seasons":
                values.append(self.color.property("catalogColor"))
            return tuple(values)

        def dirty(self):
            return self.baseline is not None and self.snapshot() != self.baseline

        def allow_leave(self):
            if not self.dirty():
                return True
            choice = QMessageBox.question(owner, "未保存の変更", f"{self.noun}の変更を保存しますか？",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if choice == QMessageBox.StandardButton.Save:
                return self.save()
            if choice == QMessageBox.StandardButton.Discard:
                self.discard()
                return True
            return False

        def sync_identifier(self):
            if key == "seasons":
                owner.selected_season_id = self.selected_id
            else:
                owner.selected_catalog_entry_ids[key] = self.selected_id

        def fill(self, entry):
            self.selected_id = self.identifier(entry) if entry else None
            self.sync_identifier()
            if key == "seasons":
                self.fields["name_input"].setText(entry.name if entry else "")
                self.fields["description_input"].setText(entry.description if entry else "")
                self.fields["type_select"].setCurrentIndex({"ranked": 0, "event": 1, "custom": 2}.get(getattr(entry, "season_type", "ranked"), 0))
                for field, attr in (("start_date_picker", "start_date"), ("end_date_picker", "end_date")):
                    d = getattr(entry, attr, date.today())
                    self.fields[field].setDate(QDate(d.year, d.month, d.day))
            else:
                owner._fill_catalog_form(key, entry)
            active = bool(entry) or self.creating
            self.title.setText(f"新規{self.noun}" if self.creating else f"{self.noun}を編集" if entry else f"{self.noun}を選択")
            self.info.setText((entry.name + (f"\n使用回数 {entry.usage_count}回" if key == "decks" else "")) if entry else
                              "内容を入力して追加します" if self.creating else "左の一覧から選ぶか、新規追加してください")
            if getattr(entry, "is_archived", False):
                self.info.setText(self.info.text() + "\nアーカイブ済み。保存すると再び有効になります。")
            self.form_widget.setVisible(active)
            self.save_button.setVisible(active)
            self.cancel.setVisible(active)
            self.extra.setVisible(bool(entry))
            self.save_button.setText("追加して保存" if self.creating else "変更を保存")
            self.remove_button.setEnabled(not getattr(entry, "is_archived", False))
            self.baseline = self.snapshot() if active else None

        def load(self):
            if self.dirty():
                return
            entries = (owner.service.list_seasons(include_archived=True) if key == "seasons" else
                       owner.service.list_decks() if key == "decks" else owner.service.list_tags())
            self.entries = {self.identifier(e): e for e in entries}
            if key == "seasons":
                owner.seasons_by_id = self.entries
            else:
                owner.catalog_entries_by_id.update(self.entries)
            self.render()
            if not self.creating:
                self.fill(self.entries.get(self.selected_id))

        def render(self):
            query, choice = self.filters
            rows = []
            for entry in self.entries.values():
                if query.casefold() not in (entry.name + " " + entry.description).casefold():
                    continue
                if key == "seasons":
                    if choice and entry.is_archived != (choice == 2):
                        continue
                    values = (entry.name, {"ranked": "ランク戦", "event": "イベント", "custom": "カスタム"}.get(entry.season_type, entry.season_type),
                              f"{entry.start_date} ～ {entry.end_date}", "アーカイブ" if entry.is_archived else "有効")
                elif key == "decks":
                    if (choice == 1 and entry.opponent_only) or (choice == 2 and not entry.opponent_only) or (choice == 3 and not entry.hidden_from_history_statistics):
                        continue
                    values = ("", entry.name, entry.description, entry.usage_count,
                              "相手専用" if entry.opponent_only else "通常", "非表示" if entry.hidden_from_history_statistics else "表示")
                else:
                    if choice and entry.deck_only != (choice == 2):
                        continue
                    values = ("", entry.name, entry.description, "デッキ専用" if entry.deck_only else "通常")
                rows.append((entry, values))
            with QSignalBlocker(self.table):
                self.table.setRowCount(len(rows))
                self.table.clearSelection()
                self.table.setCurrentCell(-1, -1)
                for row, (entry, values) in enumerate(rows):
                    for col, value in enumerate(values):
                        item = QTableWidgetItem(str(value))
                        item.setToolTip(str(value))
                        if col == 0:
                            item.setData(Qt.ItemDataRole.UserRole, self.identifier(entry))
                            if key != "seasons":
                                swatch = QPixmap(16, 16)
                                swatch.fill(QColor(entry.color or "#4f6f8f"))
                                item.setIcon(QIcon(swatch))
                                item.setToolTip(f"色: {entry.color}")
                        if key == "decks" and col == 3:
                            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                        self.table.setItem(row, col, item)
                    if self.identifier(entry) == self.selected_id:
                        self.table.selectRow(row)
            self.summary.setText(f"{len(rows)}件 / 全{len(self.entries)}件" + ("　条件に一致する項目はありません" if not rows else ""))

        def selection_changed(self):
            row = self.table.currentRow()
            item = self.table.item(row, 0) if row >= 0 else None
            identifier = item.data(Qt.ItemDataRole.UserRole) if item else None
            if identifier == self.selected_id:
                return
            if not self.allow_leave():
                self.render()
                return
            self.creating = False
            self.fill(self.entries.get(identifier))
            self.render()

        def filter_changed(self):
            target = (self.search.text(), self.filter.currentIndex())
            if not self.allow_leave():
                with QSignalBlocker(self.search), QSignalBlocker(self.filter):
                    self.search.setText(self.filters[0])
                    self.filter.setCurrentIndex(self.filters[1])
                return
            self.filters = target
            self.render()
            if not self.table.selectedItems() and not self.creating:
                self.fill(None)

        def new(self):
            if self.allow_leave():
                self.creating = True
                self.fill(None)
                self.render()
                self.fields["name_input"].setFocus()

        def discard(self):
            self.creating = False
            self.fill(self.entries.get(self.selected_id))
            self.render()

        def save(self):
            if not self.creating and self.selected_id is None:
                return False
            try:
                if key == "seasons":
                    values = owner._season_values()
                    entry = self.entries.get(self.selected_id)
                    if entry and values["season_type"] == entry.season_type:
                        values["duel_type"] = entry.duel_type
                    saved = (owner.service.add_season(**values) if self.selected_id is None else
                             owner.service.update_season(self.selected_id, **values))
                else:
                    values = owner._catalog_values(key)
                    if self.selected_id is None:
                        if key == "decks":
                            saved = owner.service.add_deck(**{k: values[k] for k in ("name", "description", "color")})
                            # 追加後の設定保存が失敗しても、再試行で二重追加しない。
                            self.selected_id = saved.entry_id
                            self.sync_identifier()
                            self.entries[saved.entry_id] = saved
                            saved = owner.service.update_deck(saved.entry_id, **values)
                        else:
                            saved = owner.service.add_tag(**values)
                    else:
                        update = owner.service.update_deck if key == "decks" else owner.service.update_tag
                        saved = update(self.selected_id, **values)
            except Exception as exc:
                owner._show_warning("保存できません", str(exc))
                return False
            self.creating = False
            self.selected_id = self.identifier(saved)
            self.sync_identifier()
            self.baseline = None
            self.load()
            owner._append_activity(f"{saved.name}を保存しました")
            if key == "seasons":
                owner._refresh_active_seasons()
            return True

        def remove(self):
            if self.selected_id is None or not self.allow_leave():
                return
            if key == "seasons":
                owner._archive_selected_season()
            else:
                owner._delete_catalog_entry(key)
            self.baseline = None
            self.selected_id = owner.selected_season_id if key == "seasons" else owner.selected_catalog_entry_ids[key]
            self.load()

        def report(self):
            if self.allow_leave():
                owner._show_selected_season_report()

    return CatalogPage()
