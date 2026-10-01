"""存储层：历史 / 预设 / 片段 / 草稿 + 导出导入往返。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import TEST_ROOT  # noqa: F401  （导入即设置环境变量）
from app.storage import (
    Database, DraftStore, FragmentStore, HistoryStore, PresetStore,
    app_data_dir, db_path, user_data_dir,
)


class StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-store-"))
        self.db = Database(self.tmp / "test.db")
        self.history = HistoryStore(self.db)
        self.presets = PresetStore(self.db)
        self.fragments = FragmentStore(self.db)
        self.drafts = DraftStore(self.db)

    def tearDown(self) -> None:
        self.db.close()


class TestPaths(unittest.TestCase):
    def test_app_dir_uses_current_name(self) -> None:
        """改名后数据目录是 mcstudio；旧目录 MCCmdGen 仍然被兼容读取。"""
        from app.storage import APP_DIR_NAME, LEGACY_APP_DIR_NAMES
        from app import branding
        self.assertEqual(APP_DIR_NAME, branding.APP_SLUG)
        self.assertEqual(app_data_dir().name, APP_DIR_NAME)
        self.assertEqual(db_path().parent, app_data_dir())
        self.assertEqual(user_data_dir().parent, app_data_dir())
        self.assertIn("MCCmdGen", LEGACY_APP_DIR_NAMES)

    def test_legacy_dir_is_used_when_new_one_absent(self) -> None:
        """新目录还没建立、旧目录有数据时，继续用旧目录（平滑过渡）。"""
        import tempfile
        from pathlib import Path
        from app.storage import _prefer_legacy, _legacy_dir, _fallback_dir
        tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-legacy-"))
        self.assertFalse(_prefer_legacy(tmp), "全新环境应使用新目录")
        old = _legacy_dir(tmp)
        old.mkdir(parents=True, exist_ok=True)
        (old / "data.db").write_bytes(b"")
        self.assertTrue(_prefer_legacy(tmp), "旧目录有数据时应优先沿用")
        _fallback_dir(tmp).mkdir(parents=True, exist_ok=True)
        self.assertFalse(_prefer_legacy(tmp), "新目录一旦建立就以新目录为准")


class TestDatabaseReconnect(unittest.TestCase):
    """回归：shutdown() 之后再次使用同一个 Database 不应抛「closed database」。"""

    def test_conn_reopens_after_close(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="mccmdgen-reopen-"))
        db = Database(tmp / "r.db")
        store = HistoryStore(db)
        self.assertIsNotNone(store.add("say hi"))
        db.close()
        # close 之后仍然可用（透明重连），且表结构已经就绪
        self.assertIsNotNone(store.add("say bye"))
        self.assertEqual([e.command for e in store.list()][0], "say bye")
        db.close()

    def test_singleton_shutdown_keeps_instance_usable(self) -> None:
        live = Database.instance()
        Database.shutdown()
        self.assertIsNone(Database._instance)
        # 老引用重连后仍然可用
        self.assertIsNotNone(live.conn.execute("SELECT 1").fetchone())
        Database.shutdown()


class TestHistory(StoreCase):
    def test_add_and_list(self) -> None:
        self.history.add("say hi", "say")
        self.history.add("summon minecraft:pig", "summon")
        rows = self.history.list()
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.history.count(), 2)

    def test_dedupe_window(self) -> None:
        self.history.add("say hi", "say")
        self.history.add("say hi", "say")
        self.assertEqual(self.history.count(), 1)

    def test_empty_ignored(self) -> None:
        self.assertIsNone(self.history.add("   ", "say"))

    def test_keyword_search(self) -> None:
        self.history.add("say hi", "say")
        self.history.add("kill @e", "kill")
        self.assertEqual(len(self.history.list(keyword="kill")), 1)

    def test_recent_values(self) -> None:
        self.history.add("give @p minecraft:stone", "give")
        vals = self.history.recent_values("give", 2)
        self.assertIn("minecraft:stone", vals)

    def test_delete_and_clear(self) -> None:
        rid = self.history.add("say hi", "say")
        self.history.delete(rid)
        self.assertEqual(self.history.count(), 0)
        self.history.add("say hi", "say")
        self.history.clear()
        self.assertEqual(self.history.count(), 0)


class TestPresets(StoreCase):
    def test_crud(self) -> None:
        pid = self.presets.add("击杀", "战斗", "kill @e", "kill")
        self.assertEqual(len(self.presets.list()), 1)
        self.presets.update(pid, "击杀2", "战斗", "kill @a", "kill")
        self.assertEqual(self.presets.list()[0].name, "击杀2")
        self.presets.delete(pid)
        self.assertEqual(self.presets.list(), [])

    def test_categories(self) -> None:
        self.presets.add("a", "战斗", "say a")
        self.presets.add("b", "工具", "say b")
        self.assertEqual(self.presets.categories(), ["工具", "战斗"])

    def test_export_import_roundtrip(self) -> None:
        self.presets.add("a", "战斗", "kill @e", "kill")
        path = self.tmp / "presets.json"
        self.assertEqual(self.presets.export_json(path), 1)

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["manifest"]["format"], 2)
        self.assertEqual(payload["manifest"]["minecraft_version"], "1.21.1")
        self.assertIn("checksum", payload["manifest"])

        other = PresetStore(Database(self.tmp / "other.db"))
        added, skipped, notes = other.import_json(path)
        self.assertEqual(added, 1)
        self.assertEqual(skipped, 0)

    def test_import_merge_skips_duplicates(self) -> None:
        self.presets.add("a", "战斗", "kill @e", "kill")
        path = self.tmp / "presets.json"
        self.presets.export_json(path)
        added, skipped, _notes = self.presets.import_json(path, merge=True)
        self.assertEqual(added, 0)
        self.assertEqual(skipped, 1)

    def test_import_detects_version_mismatch(self) -> None:
        path = self.tmp / "old.json"
        path.write_text(json.dumps({
            "manifest": {"format": 2, "minecraft_version": "1.20.4",
                         "generator": "MCCmdGen"},
            "presets": [{"name": "x", "category": "c", "command": "say x"}],
        }, ensure_ascii=False), encoding="utf-8")
        added, _skipped, notes = self.presets.import_json(path)
        self.assertEqual(added, 1)
        self.assertTrue(any("1.20.4" in n for n in notes))

    def test_import_rejects_garbage(self) -> None:
        path = self.tmp / "bad.json"
        path.write_text('{"presets": "not a list"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.presets.import_json(path)


class TestFragments(StoreCase):
    def test_crud_and_payload(self) -> None:
        fid = self.fragments.add("片段", "command",
                                 {"command": "say hi"}, "工具")
        items = self.fragments.list()
        self.assertEqual(items[0].payload["command"], "say hi")
        self.assertEqual(self.fragments.get(fid).name, "片段")
        self.fragments.update(fid, "片段2", "command",
                              {"command": "say yo"}, "工具")
        self.assertEqual(self.fragments.get(fid).name, "片段2")
        self.fragments.delete(fid)
        self.assertIsNone(self.fragments.get(fid))

    def test_filter_by_kind(self) -> None:
        self.fragments.add("a", "command", {"command": "say a"})
        self.fragments.add("b", "nbt", {"snbt": "{a:1}"})
        self.assertEqual(len(self.fragments.list(kind="nbt")), 1)

    def test_export_import(self) -> None:
        self.fragments.add("a", "command", {"command": "say a"})
        path = self.tmp / "frags.json"
        self.assertEqual(self.fragments.export_json(path), 1)
        added, skipped = self.fragments.import_json(path, merge=True)
        self.assertEqual((added, skipped), (0, 1))


class TestDrafts(StoreCase):
    def test_save_load_delete(self) -> None:
        self.drafts.save("session", {"output": "say hi"})
        self.assertEqual(self.drafts.load("session")["output"], "say hi")
        self.drafts.save("session", {"output": "say yo"})
        self.assertEqual(self.drafts.load("session")["output"], "say yo")
        self.drafts.delete("session")
        self.assertIsNone(self.drafts.load("session"))

    def test_empty_key_ignored(self) -> None:
        self.drafts.save("", {"a": 1})
        self.assertIsNone(self.drafts.load(""))

    def test_bad_payload_returns_none(self) -> None:
        self.db.conn.execute(
            "INSERT INTO drafts(key, payload) VALUES ('x', 'not json')")
        self.db.conn.commit()
        self.assertIsNone(self.drafts.load("x"))


if __name__ == "__main__":
    unittest.main()
