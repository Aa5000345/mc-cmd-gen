"""端到端一致性：对每一条注册命令跑「生成 → 反向解析 → 再生成」闭环。

这是防回归的关键测试：任何一条命令的默认输出非法、或反向解析错位，都会在这里暴露。
"""
from __future__ import annotations

import re
import unittest

import tests  # noqa: F401  （导入即设置环境变量）
from app.core import all_builders, get_builder
from app.core.datapack import detect_version
from app.core.rules import validate_text

# 默认输出里必然出现、但属于"占位示例"的取值，允许解析后不完全一致
_SAMPLE_TOKENS = ("minecraft:example", "minecraft:custom:name", "example")


def _iter_commands():
    return sorted(all_builders())


class TestDefaultOutputQuality(unittest.TestCase):
    def test_every_command_builds_non_empty(self) -> None:
        for name in _iter_commands():
            with self.subTest(command=name):
                inst = get_builder(name)()
                text = inst.build()
                self.assertTrue(text.strip(), f"/{name} 默认输出为空")
                self.assertTrue(text.startswith(name),
                                f"/{name} 默认输出未以命令名开头：{text!r}")

    def test_no_placeholder_syntax_in_output(self) -> None:
        """默认输出里不应残留 {key} / None / 双空格等拼装痕迹。"""
        for name in _iter_commands():
            inst = get_builder(name)()
            text = inst.build()
            with self.subTest(command=name, output=text):
                self.assertNotIn("None", text)
                self.assertNotIn("{'", text)
                self.assertNotIn("  ", text)
                self.assertFalse(re.search(r"\{[a-z_]+\}", text),
                                 f"/{name} 输出疑似残留占位符：{text!r}")

    def test_default_output_passes_error_level_rules(self) -> None:
        """默认输出不允许出现 error 级校验问题（warn/info 属于历史写法提示）。"""
        for name in _iter_commands():
            inst = get_builder(name)()
            errors = [i for i in inst.validate()
                      if getattr(i, "level", "error") == "error"]
            with self.subTest(command=name, output=inst.build()):
                self.assertEqual([str(e) for e in errors], [])


class TestRoundTripEveryCommand(unittest.TestCase):
    def test_default_output_roundtrips(self) -> None:
        """生成 → 解析回 builder → 再生成，文本应保持一致。"""
        failures: list[str] = []
        for name in _iter_commands():
            inst = get_builder(name)()
            original = inst.build()
            fresh = get_builder(name)()
            try:
                leftover = fresh.load_from(original)
                rebuilt = fresh.build()
            except NotImplementedError:
                continue
            except Exception as e:
                failures.append(f"/{name}: {type(e).__name__}: {e} ({original!r})")
                continue
            if rebuilt != original:
                failures.append(
                    f"/{name}: {original!r} -> {rebuilt!r} 未消费={leftover}")
        self.assertEqual(failures, [], "反向解析不一致：\n" + "\n".join(failures))

    def test_roundtrip_is_stable_when_repeated(self) -> None:
        """二次往返必须收敛（不允许每次解析都漂移）。"""
        failures: list[str] = []
        for name in _iter_commands():
            inst = get_builder(name)()
            text = inst.build()
            for _ in range(3):
                nxt = get_builder(name)()
                try:
                    nxt.load_from(text)
                except NotImplementedError:
                    break
                except Exception as e:
                    failures.append(f"/{name}: {e}")
                    break
                new_text = nxt.build()
                if new_text != text:
                    failures.append(f"/{name}: {text!r} -> {new_text!r}")
                    break
                text = new_text
        self.assertEqual(failures, [], "往返未收敛：\n" + "\n".join(failures))


class TestQueryLikeCommands(unittest.TestCase):
    """带有分支参数的命令：每条分支都要能生成且能往返。"""

    CASES = {
        "time": [{"action": "set", "value": "day"},
                 {"action": "add", "amount": "1000"},
                 {"action": "query", "value": "day"}],
        "weather": [{}, {"type": "rain", "duration": "600"}],
        "bossbar": [{"action": "add", "name": '{"text":"B"}'},
                    {"action": "set", "field": "value", "value": "50"},
                    {"action": "get"}],
        "forceload": [{"action": "query"}, {"action": "add", "from": "0 0"}],
        "xp": [{"action": "query"}, {"action": "set", "amount": "5",
                                     "unit": "levels"}],
        "schedule": [{"action": "clear", "name": "minecraft:example"},
                     {"action": "function", "name": "minecraft:example",
                      "time": "1d", "mode": "append"}],
        "worldborder": [{"action": "get"}, {"action": "set", "value": "100"}],
        "gamemode": [{"mode": "survival", "target": "@a"}],
    }

    def test_branch_output_and_roundtrip(self) -> None:
        failures: list[str] = []
        for name, branches in self.CASES.items():
            for values in branches:
                inst = get_builder(name)()
                inst.values.update(values)
                text = inst.build()
                with self.subTest(command=name, values=values):
                    self.assertTrue(text.startswith(name))
                    self.assertNotIn("  ", text)
                nxt = get_builder(name)()
                try:
                    nxt.load_from(text)
                except Exception as e:
                    failures.append(f"/{name} {values}: {e}")
                    continue
                if nxt.build() != text:
                    failures.append(f"/{name} {values}: {text!r} -> "
                                    f"{nxt.build()!r}")
        self.assertEqual(failures, [], "分支往返失败：\n" + "\n".join(failures))


class TestVersionConsistency(unittest.TestCase):
    def test_own_default_output_is_detected_as_supported(self) -> None:
        """默认输出要么被判为 1.20.5+，要么命中明确的老写法规则，不应误判。"""
        suspicious: list[str] = []
        for name in _iter_commands():
            text = get_builder(name)().build()
            version = detect_version(text)
            if version not in ("1.20.4", "1.20.5", "1.20.6", "1.21", "1.21.1"):
                suspicious.append(f"/{name}: {version}")
        self.assertEqual(suspicious, [])


class TestRuleCoverage(unittest.TestCase):
    def test_rules_detect_known_legacy_syntax(self) -> None:
        samples = {
            "give @p minecraft:book[minecraft:custom_data={Enchantments:[1]}]": "warn",
            "summon minecraft:zombie ~ ~ ~ {HandItems:[]}": "warn",
            'summon minecraft:zombie ~ ~ ~ {attributes:[{id:"minecraft:generic.max_health"}]}': "warn",
            "execute in overworld run say hi": "warn",
            "tp @p 1 2 3": "info",
        }
        for text, level in samples.items():
            with self.subTest(text=text):
                levels = [lv for lv, _m in validate_text(text)]
                self.assertIn(level, levels)

    def test_clean_command_has_no_hits(self) -> None:
        self.assertEqual(validate_text("setblock ~ ~ ~ minecraft:stone replace"), [])


if __name__ == "__main__":
    unittest.main()
