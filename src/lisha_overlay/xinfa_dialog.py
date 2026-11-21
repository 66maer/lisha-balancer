"""Review unresolved slots or edit the prepared roster."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout

from .xinfa import catalogue


class XinfaDialog(QDialog):
    def __init__(self, groups, draft, parent=None, *, unresolved_only=False, frame=None, layout=None, candidates=None):
        super().__init__(parent)
        self.setWindowTitle('补认心法' if unresolved_only else '核对心法名单')
        self.setObjectName('xinfaDialog')
        self.setStyleSheet("""
            QDialog#xinfaDialog { background: #14202b; }
            QComboBox {
                color: #e8edf5; background: #203040; border: 1px solid #486175;
                border-radius: 4px; padding: 3px 6px;
            }
            QComboBox:focus { border-color: #63dfcb; }
            QComboBox QAbstractItemView {
                color: #e8edf5; background: #203040;
                selection-color: #c5fff0; selection-background-color: #294b44;
            }
        """)
        content = QVBoxLayout(self)
        content.addWidget(QLabel('请补齐所有待确认心法。候选按识别分数排序。' if unresolved_only
                                 else '核对或修改心法。换人或换位后请重新识别。'))
        grid = QGridLayout()
        self.slots = {}
        specs = catalogue()
        candidates = candidates or {}
        entries = [(group, slot, f'g{group}p{slot}') for group in groups for slot in range(1, 6)]
        self.confirmed = {key: draft[key] for _, _, key in entries if draft.get(key) in specs}
        visible_entries = [(group, slot, key) for group, slot, key in entries
                           if not unresolved_only or key not in self.confirmed]
        cells = {cell.key: cell.rect for cell in layout.cells(frame.size())} if frame is not None else {}
        columns = min(5, max(1, len(visible_entries)))
        if not unresolved_only:
            for column, group in enumerate(groups):
                grid.addWidget(QLabel(f'{group}队'), 0, column)
        for index, (group, slot, key) in enumerate(visible_entries):
            combo = QComboBox()
            combo.setAccessibleName(f'{group}队{slot}号心法')
            combo.setPlaceholderText('待确认')
            ordered = dict.fromkeys((*candidates.get(key, ()), *specs))
            for code in ordered:
                if code not in specs:
                    continue
                spec = specs[code]
                combo.addItem(spec.nickname if spec.nickname == spec.name else f'{spec.nickname} · {spec.name}', code)
            combo.setCurrentIndex(combo.findData(self.confirmed.get(key)))
            self.slots[key] = combo
            if unresolved_only:
                row, column = divmod(index, columns)
                label = QLabel(f'{group}队{slot}号')
                label.setBuddy(combo)
                grid.addWidget(label, row * 2, column)
                choice = QHBoxLayout()
                if key in cells:
                    x, y, _, height = cells[key].getRect()
                    rect = QRectF(x, y, .64 * height, .64 * height).toAlignedRect()
                    icon = QLabel()
                    icon.setPixmap(QPixmap.fromImage(frame.copy(rect.intersected(frame.rect()))).scaled(
                        24, 24, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    choice.addWidget(icon)
                choice.addWidget(combo, 1)
                grid.addLayout(choice, row * 2 + 1, column)
            else:
                grid.addWidget(combo, slot, groups.index(group))
        content.addLayout(grid)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.confirm_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm_button.setText('确认名单')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('忽略')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        content.addWidget(buttons)
        for combo in self.slots.values():
            combo.currentIndexChanged.connect(self._update_confirmation)
        self._update_confirmation()

    def _update_confirmation(self):
        self.confirm_button.setEnabled(all(combo.currentIndex() >= 0 for combo in self.slots.values()))

    def accept(self):
        if self.confirm_button.isEnabled():
            super().accept()

    def roster(self):
        roster = {key: value for key, value in self.confirmed.items() if key not in self.slots}
        roster.update((key, combo.currentData()) for key, combo in self.slots.items() if combo.currentData())
        return roster
