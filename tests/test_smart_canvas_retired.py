import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main


class SmartCanvasRetiredTests(unittest.TestCase):
    def test_editor_script_and_css_are_gone(self):
        self.assertFalse((ROOT / "static" / "js" / "smart-canvas.js").exists())
        self.assertFalse((ROOT / "static" / "css" / "smart-canvas.css").exists())

    def test_old_url_redirects_to_workspace(self):
        html = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        self.assertIn("/static/canvas-list.html", html)
        self.assertNotIn("smart-canvas.js", html)

    def test_workspace_no_longer_creates_smart_canvases(self):
        listing = (ROOT / "static" / "js" / "canvas-list.js").read_text(encoding="utf-8")
        classic = (ROOT / "static" / "js" / "canvas.js").read_text(encoding="utf-8")
        self.assertNotIn('data-kind="smart"', listing)
        self.assertNotIn("kind: isSmart ? 'smart' : 'classic'", listing)
        self.assertIn("kind: 'classic'", listing)
        self.assertNotIn("openSmartCanvasPage", classic)
        self.assertNotIn("/static/smart-canvas.html", classic)
        self.assertIn("kind:'classic'", classic.replace(" ", ""))

    def test_classic_prompt_template_i18n_keys_remain(self):
        i18n = (ROOT / "static" / "js" / "i18n" / "smart-canvas.js").read_text(encoding="utf-8")
        canvas = (ROOT / "static" / "js" / "canvas.js").read_text(encoding="utf-8")
        self.assertIn("smart.promptTemplateLibrary", i18n)
        self.assertIn("smart.tplCatView", i18n)
        self.assertIn("tr('smart.tplCatView')", canvas)

    def test_backend_drops_smart_canvas_routes(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertNotIn('/api/smart-canvas/prompt-templates', source)
        self.assertNotIn('/api/smart-canvas/group-export', source)
        self.assertIn("def normalize_canvas_kind", source)

    def test_asset_copy_pastes_into_classic_canvas(self):
        assets = (ROOT / "static" / "js" / "asset-manager.js").read_text(encoding="utf-8")
        classic = (ROOT / "static" / "js" / "canvas.js").read_text(encoding="utf-8")
        self.assertNotIn("去智能画布", assets)
        self.assertIn("打开画布后按 Ctrl+V 粘贴", assets)
        self.assertIn("smart_canvas_asset_inbox", assets)
        self.assertIn("smart_canvas_asset_inbox", classic)
        self.assertIn("function pasteCanvasAssetInbox", classic)
        self.assertIn("从画布输出保存到素材库", assets)
        self.assertNotIn("从智能画布输出保存到素材库", assets)


class NewCanvasIgnoresSmartKindTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.canvases = self.data / "canvases"
        self.canvases.mkdir(parents=True, exist_ok=True)
        self.projects_path = self.data / "projects.json"
        self.patches = [
            patch.object(main, "DATA_DIR", str(self.data)),
            patch.object(main, "CANVAS_DIR", str(self.canvases)),
            patch.object(main, "PROJECTS_PATH", str(self.projects_path)),
        ]
        for item in self.patches:
            item.start()
        main._canvas_record_cache.update({"dir": None, "sig": None, "files": {}, "live": [], "trash": []})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_new_canvas_ignores_smart_kind(self):
        canvas = main.new_canvas("", "sparkles", "smart")
        self.assertEqual(canvas["kind"], "classic")
        self.assertEqual(canvas["title"], "未命名画布")
        stored = json.loads((self.canvases / f"{canvas['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["kind"], "classic")
        leftover = main.canvas_record({"id": "legacy-smart", "kind": "smart", "title": "旧智能", "nodes": []})
        self.assertEqual(leftover["kind"], "smart")


if __name__ == "__main__":
    unittest.main()
