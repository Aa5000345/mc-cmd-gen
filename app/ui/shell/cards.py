"""壳层通用部件：可点击卡片、区块标题、引导徽章。

这一层是 2.1「重新设计」的视觉基元，只负责外观与交互骨架，
业务动作全部通过信号交回 `MainWindow`，所以壳层不依赖任何核心模块。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)


class SectionTitle(QLabel):
    """小区块标题（全大写风格，弱化但可扫读）。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("HomeSection")


class Card(QFrame):
    """任务卡片：标题 + 一行说明 + 可选快捷键胶囊，整块可点。"""

    clicked = Signal()

    def __init__(self, title: str, desc: str = "", shortcut: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setProperty("hover", "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(92)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")
        self.title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        head.addWidget(self.title_label)
        head.addStretch(1)
        if shortcut:
            chip = QLabel(shortcut)
            chip.setObjectName("CardKey")
            chip.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            head.addWidget(chip)
        outer.addLayout(head)

        self.desc_label = QLabel(desc)
        self.desc_label.setObjectName("CardDesc")
        self.desc_label.setWordWrap(True)
        self.desc_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        outer.addWidget(self.desc_label)
        outer.addStretch(1)

    # ---- 视觉状态 ----
    def _set_hover(self, on: bool) -> None:
        if self.property("hover") == ("true" if on else "false"):
            return
        self.setProperty("hover", "true" if on else "false")
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self._set_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self._set_hover(False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                           Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def set_badge(self, text: str) -> None:
        """在说明后面追加一个状态徽章（如「已完成」）。"""
        self.desc_label.setText(f"{self._base_desc}　{text}" if text
                                else self._base_desc)

    @property
    def _base_desc(self) -> str:
        return getattr(self, "_desc_text", self.desc_label.text())

    def set_desc(self, desc: str) -> None:
        self._desc_text = desc
        self.desc_label.setText(desc)


class Banner(QFrame):
    """顶部提示条：一句话说明 + 一到两个动作按钮。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HomeBanner")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(10)
        self.label = QLabel(text)
        self.label.setObjectName("BannerText")
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        self._row = row

    def add_action(self, widget: QWidget) -> None:
        self._row.addWidget(widget)

    def set_text(self, text: str) -> None:
        self.label.setText(text)


__all__ = ["Banner", "Card", "SectionTitle"]
