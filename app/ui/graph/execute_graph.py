"""Execute 节点图（只读可视化）。"""
from __future__ import annotations

from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import (
    QGraphicsScene, QGraphicsView, QGraphicsRectItem,
    QGraphicsTextItem, QGraphicsLineItem, QWidget, QVBoxLayout,
)


class ExecuteGraphView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.view)

    def set_nodes(self, nodes) -> None:
        self.scene.clear()
        if not nodes:
            text = QGraphicsTextItem("没有 execute 节点。")
            text.setDefaultTextColor(QColor("#8B949E"))
            self.scene.addItem(text)
            return

        x, y = 0, 0
        prev_right = None
        for n in nodes:
            rect = QGraphicsRectItem(x, y, 240, 64)
            rect.setBrush(QBrush(QColor("#1F6FEB")))
            rect.setPen(QPen(QColor("#30363D")))
            self.scene.addItem(rect)

            summary = n.summary()
            if len(summary) > 40:
                summary = summary[:37] + "…"
            text = QGraphicsTextItem(summary, rect)
            text.setDefaultTextColor(QColor("#FFFFFF"))
            text.setPos(x + 8, y + 8)

            if prev_right is not None:
                line = QGraphicsLineItem(prev_right, y + 32, x, y + 32)
                line.setPen(QPen(QColor("#8B949E"), 2))
                self.scene.addItem(line)

            prev_right = x + 240
            x += 280


__all__ = ["ExecuteGraphView"]