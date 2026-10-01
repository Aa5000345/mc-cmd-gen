"""Builder 基础设施（无内部依赖，供 core/__init__.py 与 core/builders 共同引用）。

放在独立模块是为了打破循环导入：
    core/__init__.py  →  core.builders  →  core.base
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Type


# ---------- 上下文 ----------

@dataclass
class BuildContext:
    version: str = "1.21.1"
    extras: dict[str, Any] = field(default_factory=dict)


# ---------- 校验分级 ----------

@dataclass
class ValidationIssue(str):
    """带级别的校验信息（str 子类，兼容旧的字符串用法）。"""

    level: str = "error"

    def __new__(cls, message: str, level: str = "error"):
        obj = super().__new__(cls, message)
        obj.level = level
        return obj

    def __init__(self, message: str, level: str = "error") -> None:
        # str 子类：内容已在 __new__ 里确定，这里只需要保留签名
        super().__init__()

    @property
    def is_error(self) -> bool:
        return self.level == "error"


# ---------- 基类 ----------

class CommandBuilder(ABC):
    command_name: str = ""
    display_name: str = ""
    category: str = "其他"
    version: str = "1.21.1"

    @abstractmethod
    def build(self, ctx: BuildContext | None = None) -> str: ...

    def load_from(self, command: str) -> None:
        raise NotImplementedError(f"{self.command_name} 不支持反向解析")

    def supports_parse(self) -> bool:
        """是否实现了反向解析（子类覆盖 load_from 后自动为 True）。"""
        return type(self).load_from is not CommandBuilder.load_from

    def validate(self, ctx: BuildContext | None = None) -> list[ValidationIssue]:
        return []

    def status_summary(self) -> str:
        return ""


# ---------- 注册表 ----------

_REGISTRY: dict[str, Type[CommandBuilder]] = {}


def register(cls: Type[CommandBuilder]) -> Type[CommandBuilder]:
    if not cls.command_name:
        raise ValueError(f"{cls.__name__} 未设置 command_name")
    _REGISTRY[cls.command_name] = cls
    return cls


def get_builder(name: str) -> Type[CommandBuilder] | None:
    return _REGISTRY.get(name)


def all_builders() -> dict[str, Type[CommandBuilder]]:
    return dict(_REGISTRY)


def iter_builders():
    return _REGISTRY.values()


def command_names() -> list[str]:
    return sorted(_REGISTRY)


__all__ = [
    "BuildContext", "ValidationIssue", "CommandBuilder",
    "register", "get_builder", "all_builders", "iter_builders", "command_names",
]
