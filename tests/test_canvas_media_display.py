import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static" / "js" / "canvas-media-display.js"
CANVAS_JS = ROOT / "static" / "js" / "canvas.js"
CANVAS_HTML = ROOT / "static" / "canvas.html"


def eval_module(expr: str):
    script = f"""
const api = require({json.dumps(str(MODULE))});
const value = {expr};
if (value && typeof value.then === 'function') {{
    value.then((resolved) => {{
        process.stdout.write(JSON.stringify(resolved));
    }});
}} else {{
    process.stdout.write(JSON.stringify(value));
}}
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    return json.loads(result.stdout or "null")


class CanvasMediaDisplayTests(unittest.TestCase):
    def test_html_loads_module_before_canvas(self):
        html = CANVAS_HTML.read_text(encoding="utf-8")
        media_at = html.find("canvas-media-display.js")
        canvas_at = html.find("/static/js/canvas.js")
        self.assertGreater(media_at, 0)
        self.assertGreater(canvas_at, media_at)

    def test_canvas_no_longer_swaps_original_on_zoom(self):
        source = CANVAS_JS.read_text(encoding="utf-8")
        self.assertNotIn("CANVAS_HIGH_RES_ZOOM_THRESHOLD", source)
        self.assertNotIn("preloadCanvasSelectedHighRes", source)
        self.assertNotIn("canvasImageNearViewport", source)
        self.assertNotIn("syncCanvasSelectedImageResolution", source)
        self.assertNotIn("loadCanvasOriginalImageDimensions(original)", source)
        self.assertIn("syncCanvasMediaDisplay", source)
        self.assertIn("transplantReusableMedia", source)
        self.assertIn("enforceSingleLiveVideo", source)

    def test_never_swap_canvas_preview_to_original(self):
        self.assertFalse(eval_module("api.shouldSwapCanvasImageToOriginal()"))

    def test_world_view_rect_uses_viewport_math(self):
        rect = eval_module("api.worldViewRect({x: -200, y: -80, scale: 2}, 800, 600)")
        self.assertEqual(rect, {"x": 100, "y": 40, "w": 400, "h": 300})

    def test_node_world_rect_falls_back_without_layout(self):
        rect = eval_module("api.nodeWorldRect({x: 10, y: 20, type: 'output'}, {w: 460, h: 0})")
        self.assertEqual(rect["x"], 10)
        self.assertEqual(rect["y"], 20)
        self.assertEqual(rect["w"], 460)
        self.assertGreaterEqual(rect["h"], eval_module("api.FALLBACK_NODE_HEIGHT"))

    def test_near_view_uses_world_coords_not_dom_rects(self):
        visible = eval_module(
            "api.nodeNearWorldView({x: 120, y: 80, w: 260, h: 336}, {x: 0, y: 0, scale: 1}, 800, 600, 220, {w: 260, h: 336})"
        )
        hidden = eval_module(
            "api.nodeNearWorldView({x: 4000, y: 3000, w: 260, h: 336}, {x: 0, y: 0, scale: 1}, 800, 600, 220, {w: 260, h: 336})"
        )
        self.assertTrue(visible)
        self.assertFalse(hidden)

    def test_reusable_media_covers_image_and_output_nodes(self):
        self.assertTrue(eval_module("api.nodeHasReusableMedia({type: 'image', url: '/output/a.png'})"))
        self.assertTrue(eval_module("api.nodeHasReusableMedia({type: 'output'})"))
        self.assertFalse(eval_module("api.nodeHasReusableMedia({type: 'prompt'})"))

    def test_media_signature_prefers_original_url(self):
        signature = eval_module(
            "api.mediaSignature({tagName: 'IMG', dataset: {previewSrc: '/api/media-preview?w=768&url=%2Foutput%2Fa.png', originalSrc: '/output/a.png', url: '/output/a.png'}})"
        )
        self.assertEqual(signature, "img:/output/a.png")

    def test_output_grid_eager_count_caps_at_eight(self):
        self.assertEqual(eval_module("api.OUTPUT_GRID_EAGER_COUNT"), 8)
        self.assertEqual(eval_module("api.outputEagerMediaCount(20, {})"), 8)
        self.assertEqual(eval_module("api.outputEagerMediaCount(3, {})"), 3)
        self.assertEqual(eval_module("api.outputEagerMediaCount(20, {expanded: true})"), 20)
        self.assertEqual(eval_module("api.outputEagerMediaCount(20, {gridSplit: true})"), 20)

    def test_output_visible_range_prefers_newest(self):
        self.assertEqual(eval_module("api.outputVisibleRange(9, {})"), {"start": 1, "end": 9, "hidden": 1})
        self.assertEqual(eval_module("api.outputVisibleRange(8, {})"), {"start": 0, "end": 8, "hidden": 0})
        self.assertEqual(eval_module("api.outputVisibleRange(20, {})"), {"start": 12, "end": 20, "hidden": 12})
        self.assertEqual(eval_module("api.outputVisibleRange(3, {})"), {"start": 0, "end": 3, "hidden": 0})
        self.assertEqual(eval_module("api.outputVisibleRange(20, {expanded: true})"), {"start": 0, "end": 20, "hidden": 0})
        self.assertEqual(eval_module("api.outputVisibleRange(20, {gridSplit: true})"), {"start": 0, "end": 20, "hidden": 0})

    def test_restore_preview_skips_empty_src(self):
        self.assertFalse(eval_module("api.shouldRestorePreviewSrc('', '/api/media-preview?w=768&url=%2Fa.png')"))
        self.assertTrue(eval_module("api.shouldRestorePreviewSrc('/output/a.png', '/api/media-preview?w=768&url=%2Fa.png')"))
        self.assertFalse(eval_module("api.shouldRestorePreviewSrc('/api/media-preview?w=768&url=%2Fa.png', '/api/media-preview?w=768&url=%2Fa.png')"))

    def test_canvas_wires_phase_two_hooks(self):
        source = CANVAS_JS.read_text(encoding="utf-8")
        css = (ROOT / "static" / "css" / "canvas.css").read_text(encoding="utf-8")
        html = CANVAS_HTML.read_text(encoding="utf-8")
        self.assertIn("syncMountedPreviews", source)
        self.assertIn("canvas-media-deferred", source)
        self.assertIn("output-grid-more", source)
        self.assertIn("outputVisibleRangeForNode", source)
        self.assertIn("outputEagerMountCount", source)
        self.assertNotIn("mount:index < eager", source)
        self.assertIn("content-visibility: auto", css)
        self.assertIn("canvas-media-display.js", html)


if __name__ == "__main__":
    unittest.main()
