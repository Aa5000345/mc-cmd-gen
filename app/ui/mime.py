"""统一拖拽 MIME 协议。"""
from __future__ import annotations
import json

from PySide6.QtCore import QMimeData

MIME_FRAGMENT = "application/x-mccmdgen-fragment"
MIME_COMMAND = "application/x-mccmdgen-command"
MIME_NBT = "application/x-mccmdgen-nbt"
MIME_TARGET = "application/x-mccmdgen-target"


def make_mime(kind: str, payload: dict, text: str = "") -> QMimeData:
    mime = QMimeData()
    mime.setData(kind, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if text:
        mime.setText(text)
    return mime


def read_mime(mime: QMimeData, kind: str) -> dict | None:
    if not mime.hasFormat(kind):
        return None
    try:
        data = json.loads(bytes(mime.data(kind)).decode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


__all__ = ["MIME_FRAGMENT", "MIME_COMMAND", "MIME_NBT", "MIME_TARGET",
           "make_mime", "read_mime"]