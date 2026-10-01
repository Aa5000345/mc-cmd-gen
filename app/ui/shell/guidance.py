"""上下文引导条：永远告诉用户「现在在哪一步、下一步点哪里」。

它固定在主窗口底部（分隔条与底部页签之间），由主窗口根据当前状态刷新：

* 命令面板 → 「① 选命令 ② 填参数 ③ 复制到游戏」；
* 输出区有命令 → 「复制命令」/「→ 存档」两个动作；
* 世界编辑器没打开存档 → 「打开世界存档…」；
* 存档只读 / 有未应用修改 → 「解锁编辑」/「预览差异」。

按钮全部由调用方注入，引导层不认识任何业务对象。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget,
)


class GuideStrip(QFrame):
    """底部引导条：左侧步骤胶囊 + 中间一句话 + 右侧动作按钮。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("GuideStrip")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(38)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 5, 12, 5)
        row.setSpacing(10)
        self._row = row

        self.steps_box = QWidget()
        self._steps_layout = QHBoxLayout(self.steps_box)
        self._steps_layout.setContentsMargins(0, 0, 0, 0)
        self._steps_layout.setSpacing(6)
        row.addWidget(self.steps_box)

        self.text_label = QLabel("")
        self.text_label.setObjectName("GuideText")
        self.text_label.setWordWrap(False)
        row.addWidget(self.text_label, 1)

        self.actions_box = QWidget()
        self._actions_layout = QHBoxLayout(self.actions_box)
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        self._actions_layout.setSpacing(6)
        row.addWidget(self.actions_box)

        self._chips: list[QLabel] = []
        self._buttons: list[QPushButton] = []
        self._steps_sig: tuple | None = None
        self._hint_sig: tuple | None = None

    # ------------------------------------------------------------------
    # 步骤胶囊
    # ------------------------------------------------------------------
    def set_steps(self, steps: list[str], current: int = -1,
                  done_until: int = -1) -> None:
        """显示步骤条；``current`` 之前的步骤标记为完成。

        幂等：状态没变就直接返回 —— 引导条会被「输出区每敲一个字」触发，
        不设短路的话每敲一个键都要销毁重建一排控件。
        """
        signature = (tuple(steps), current, done_until)
        if signature == self._steps_sig:
            return
        self._steps_sig = signature
        for chip in self._chips:
            self._steps_layout.removeWidget(chip)
            chip.setParent(None)          # 立刻脱离父子关系，避免新旧控件重叠
            chip.deleteLater()
        self._chips = []
        for index, text in enumerate(steps):
            chip = QLabel(f"{index + 1}. {text}")
            chip.setObjectName("GuideStep")
            if index <= done_until or index < current:
                state = "done"
            elif index == current:
                state = "current"
            else:
                state = "todo"
            chip.setProperty("state", state)
            self._steps_layout.addWidget(chip)
            self._chips.append(chip)
        self.steps_box.setVisible(bool(steps))

    def clear_steps(self) -> None:
        self.set_steps([])

    # ------------------------------------------------------------------
    # 提示 + 动作
    # ------------------------------------------------------------------
    def set_hint(self, text: str,
                 actions: list[tuple[str, object]] | None = None,
                 *, primary: int = 0) -> None:
        """设置提示文案与动作按钮；``primary`` 指定哪个按钮用主按钮样式。

        幂等：文案与按钮标签都没变时不再重建按钮（否则鼠标悬停/键盘输入
        造成的重复调用会不断 deleteLater，既闪又慢）。
        """
        actions = list(actions or [])
        signature = (text, tuple(str(a[0]) for a in actions), primary)
        if signature == self._hint_sig:
            return
        self._hint_sig = signature
        self.text_label.setText(text)
        self.text_label.setToolTip(text)
        for button in self._buttons:
            self._actions_layout.removeWidget(button)
            button.setParent(None)        # 同上：别让旧按钮留在布局里
            button.deleteLater()
        self._buttons = []
        for index, item in enumerate(actions):
            label, callback = item[0], item[1]
            if not callable(callback):
                # 拿不到回调（属性改名 / 传了 None）时干脆不给按钮：
                # 造一个点了没反应的按钮比没有按钮更糟
                continue
            button = QPushButton(label)
            button.setObjectName("Primary" if index == primary else "Secondary")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(callback)
            self._actions_layout.addWidget(button)
            self._buttons.append(button)
        self.actions_box.setVisible(bool(self._buttons))

    def reset(self) -> None:
        """忘掉缓存的状态（换主题/重建引导条后强制重画）。"""
        self._steps_sig = None
        self._hint_sig = None

    def set_visible_hint(self, text: str) -> None:
        self.set_hint(text, [])

    def current_text(self) -> str:
        return self.text_label.text()

    def action_labels(self) -> list[str]:
        return [button.text() for button in self._buttons]


__all__ = ["GuideStrip"]
