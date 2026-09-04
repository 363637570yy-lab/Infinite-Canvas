import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "static" / "index.html"
CANVAS_JS = ROOT / "static" / "js" / "canvas.js"


class LocalToolsRetiredTests(unittest.TestCase):
    def test_studio_hides_local_tool_nav_and_defaults_to_canvas(self):
        html = INDEX.read_text(encoding="utf-8")
        self.assertNotIn("switchUI(this, 'zimage')", html)
        self.assertNotIn("switchUI(this, 'enhance')", html)
        self.assertNotIn("switchUI(this, 'klein')", html)
        self.assertNotIn("switchUI(this, 'angle')", html)
        self.assertNotIn("frame-zimage", html)
        self.assertNotIn('data-i18n="nav.localTools"', html)
        self.assertIn("const DEFAULT_PAGE_ID = 'canvas'", html)
        self.assertIn("switchUI(this, 'canvas')", html)
        self.assertIn('id="frame-canvas"', html)
        self.assertIn('id="frame-canvas" data-src="/static/canvas-list.html', html)
        self.assertIn("switchUI(this, 'online')", html)
        self.assertIn("/generate", CANVAS_JS.read_text(encoding="utf-8"))
        self.assertIn("/api/ms/generate", CANVAS_JS.read_text(encoding="utf-8"))
        self.assertIn("/api/angle/generate", CANVAS_JS.read_text(encoding="utf-8"))

    def test_old_local_pages_redirect_to_workspace(self):
        for name in ("zimage.html", "enhance.html", "klein.html", "angle.html"):
            html = (ROOT / "static" / name).read_text(encoding="utf-8")
            self.assertIn("/static/canvas-list.html", html)
            self.assertLess(len(html), 800)

    def test_classic_canvas_comfy_endpoints_remain(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('@app.post("/generate")', source)
        self.assertIn('@app.post("/api/ms/generate")', source)
        self.assertIn('@app.post("/api/angle/generate")', source)


if __name__ == "__main__":
    unittest.main()
