"""在线自更新默认关闭。

更新源指向的是上游作者的仓库/空间，本项目是它的产品化分支。一旦有人点更新，
上游代码会覆盖本分支的协议实现和落盘逻辑，所以默认必须是关的，
而且「关」要覆盖全部外呼面：落地、回滚、版本比对、连通性探测，
以及 app-info 下发给浏览器的远端源地址（前端曾据此绕过后端直连）。
"""

import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from fastapi import HTTPException


REMOTE_URL_KEYS = ("repo_url", "version_url", "tree_url", "update_notes_url")


class DefaultOffTests(unittest.TestCase):
    def test_disabled_by_default(self):
        """没设环境变量时必须是关的 —— 这是整个防护的前提。"""
        self.assertFalse(main.SELF_UPDATE_ENABLED)

    def test_guard_raises_403(self):
        with patch.object(main, "SELF_UPDATE_ENABLED", False):
            with self.assertRaises(HTTPException) as ctx:
                main.require_self_update_enabled()
        self.assertEqual(ctx.exception.status_code, 403)

    def test_guard_passes_when_enabled(self):
        with patch.object(main, "SELF_UPDATE_ENABLED", True):
            self.assertIsNone(main.require_self_update_enabled())


class GuardedEndpointTests(unittest.TestCase):
    """每个会写代码目录或外呼上游的端点都要被挡住。"""

    def _assert_403(self, call):
        with patch.object(main, "SELF_UPDATE_ENABLED", False):
            with self.assertRaises(HTTPException) as ctx:
                call()
        self.assertEqual(ctx.exception.status_code, 403)

    def test_update_from_github_blocked(self):
        self._assert_403(lambda: main.update_from_github(main.UpdateRequest()))

    def test_rollback_blocked(self):
        self._assert_403(lambda: main.rollback_update(main.RollbackRequest(name="20260101-000000")))

    def test_backups_blocked(self):
        self._assert_403(main.get_update_backups)

    def test_connectivity_blocked(self):
        self._assert_403(main.update_connectivity)

    def test_connectivity_probe_blocked(self):
        self._assert_403(lambda: main.update_connectivity_probe("GitHub 版本文件"))

    def test_update_lock_not_held_after_block(self):
        """守卫必须在拿锁之前拒绝，否则一次调用就把更新锁永久占住。"""
        with patch.object(main, "SELF_UPDATE_ENABLED", False):
            with self.assertRaises(HTTPException):
                main.update_from_github(main.UpdateRequest())
        acquired = main.UPDATE_LOCK.acquire(blocking=False)
        self.assertTrue(acquired)
        if acquired:
            main.UPDATE_LOCK.release()


class AppInfoTests(unittest.TestCase):
    def test_no_remote_urls_when_disabled(self):
        """前端有「后端不可用就浏览器直连 VERSION」的兜底，下发地址等于绕过开关。"""
        with patch.object(main, "SELF_UPDATE_ENABLED", False):
            info = main.app_info()
        self.assertFalse(info["self_update_enabled"])
        self.assertNotIn("sources", info)
        for key in REMOTE_URL_KEYS:
            self.assertNotIn(key, info)
        self.assertNotIn("hero8152", repr(info))
        self.assertNotIn("daniel8152", repr(info))

    def test_version_still_reported(self):
        with patch.object(main, "SELF_UPDATE_ENABLED", False):
            info = main.app_info()
        self.assertTrue(str(info.get("version") or "").strip())
        self.assertIn("update_notes", info)

    def test_sources_restored_when_enabled(self):
        with patch.object(main, "SELF_UPDATE_ENABLED", True):
            info = main.app_info()
        self.assertTrue(info["self_update_enabled"])
        self.assertIn("sources", info)
        self.assertIn("github", info["sources"])


class CheckUpdateTests(unittest.TestCase):
    def test_reports_no_update_without_network(self):
        """关闭时返回结构完整的结果，而不是报错 —— 报错反而会激活前端的直连兜底。"""
        with patch.object(main, "SELF_UPDATE_ENABLED", False):
            with patch.object(main, "fetch_remote_version", side_effect=AssertionError("不得外呼更新源")):
                data = main.check_update()
        self.assertFalse(data["update_available"])
        self.assertTrue(data["reachable"])
        self.assertEqual(data["latest"], {})
        self.assertFalse(data["self_update_enabled"])
        self.assertTrue(str(data.get("current") or "").strip())

    def test_no_remote_urls_leaked(self):
        with patch.object(main, "SELF_UPDATE_ENABLED", False):
            data = main.check_update()
        self.assertNotIn("hero8152", repr(data))
        self.assertNotIn("daniel8152", repr(data))


class ValidateStagedUpdateTests(unittest.TestCase):
    """落地是先备份再逐个 os.replace，写到一半才发现包是坏的就起不来了。"""

    def _stage(self, root, main_src="print('ok')\n", version="2026.01.01", extras=None):
        root.mkdir(parents=True, exist_ok=True)
        if main_src is not None:
            (root / "main.py").write_text(main_src, encoding="utf-8")
        if version is not None:
            (root / "VERSION").write_text(version, encoding="utf-8")
        for rel, body in (extras or {}).items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return root

    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "staging"

    def tearDown(self):
        self.temp.cleanup()

    def test_accepts_complete_package(self):
        self._stage(self.root, extras={"static/js/canvas.js": "// ok"})
        main.validate_staged_update(str(self.root), ["main.py", "VERSION"], ["static/js/canvas.js"])

    def test_rejects_missing_main(self):
        self._stage(self.root, main_src=None)
        with self.assertRaises(RuntimeError):
            main.validate_staged_update(str(self.root), ["VERSION"], [])

    def test_rejects_missing_version(self):
        self._stage(self.root, version=None)
        with self.assertRaises(RuntimeError):
            main.validate_staged_update(str(self.root), ["main.py"], [])

    def test_rejects_truncated_main(self):
        """半截下载最典型的形态：文件在，但语法断了。"""
        self._stage(self.root, main_src=textwrap.dedent("def broken(:\n    pass\n"))
        with self.assertRaises(RuntimeError):
            main.validate_staged_update(str(self.root), ["main.py", "VERSION"], [])

    def test_rejects_bad_version_text(self):
        self._stage(self.root, version="<html>404 Not Found</html>")
        with self.assertRaises(RuntimeError):
            main.validate_staged_update(str(self.root), ["main.py", "VERSION"], [])

    def test_rejects_missing_listed_file(self):
        self._stage(self.root)
        with self.assertRaises(RuntimeError):
            main.validate_staged_update(str(self.root), ["main.py", "VERSION"], ["static/js/canvas.js"])


if __name__ == "__main__":
    unittest.main()
