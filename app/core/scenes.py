"""会话场景管理。"""
from __future__ import annotations
import json
from pathlib import Path

from app.storage import app_data_dir


def scenes_dir() -> Path:
    p = app_data_dir() / "scenes"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_scene(name: str, payload: dict) -> Path:
    path = scenes_dir() / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def load_scene(name: str) -> dict | None:
    path = scenes_dir() / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_scenes() -> list[str]:
    return sorted(p.stem for p in scenes_dir().glob("*.json"))


def delete_scene(name: str) -> None:
    path = scenes_dir() / f"{name}.json"
    if path.exists():
        path.unlink()


__all__ = ["scenes_dir", "save_scene", "load_scene", "list_scenes", "delete_scene"]