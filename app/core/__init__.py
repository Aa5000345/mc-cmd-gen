"""core 包入口：统一导出 Builder 基础设施 / Schema 机制 / 校验规则，并完成注册引导。

模块分工（重构后，打破 core/__init__ 与 core/builders 的循环导入）：
    base.py    —— BuildContext / ValidationIssue / CommandBuilder / 注册表
    schema.py  —— ArgSpec / SchemaBuilder / register_schema / load_schemas / split_command
    rules.py   —— 正则校验规则
    builders/  —— 手写核心命令 Builder（/summon、/give、/execute）
"""
from __future__ import annotations

from app.core.base import (
    BuildContext, CommandBuilder, ValidationIssue,
    all_builders, command_names, get_builder, iter_builders, register,
)
from app.core.rules import DEFAULT_RULES, load_rules, validate_text
from app.core.schema import (
    ArgSpec, SchemaBuilder, count_brackets, load_schemas,
    register_schema, split_command,
)


def _bootstrap() -> None:
    """延迟导入触发手写 Builder 注册，再加载 schema 命令。"""
    from app.core import builders  # noqa: F401
    load_schemas()


_bootstrap()


__all__ = [
    # base
    "BuildContext", "CommandBuilder", "ValidationIssue",
    "register", "get_builder", "all_builders", "iter_builders", "command_names",
    # schema
    "SchemaBuilder", "ArgSpec", "register_schema", "load_schemas",
    "split_command", "count_brackets",
    # rules
    "DEFAULT_RULES", "load_rules", "validate_text",
]
