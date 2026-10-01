"""向后兼容：校验规则已移动到 app.core.rules（避免循环导入）。

保留本模块是为了兼容外部 `from app.core.builders.rules import validate_text`。
"""
from app.core.rules import DEFAULT_RULES, load_rules, validate_text

__all__ = ["DEFAULT_RULES", "load_rules", "validate_text"]
