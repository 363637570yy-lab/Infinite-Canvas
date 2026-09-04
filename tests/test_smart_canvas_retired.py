import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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


if __name__ == "__main__":
    unittest.main()
