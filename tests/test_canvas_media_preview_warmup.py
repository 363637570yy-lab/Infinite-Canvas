import asyncio
import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class MediaPreviewWarmupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.generated = self.assets / "output"
        self.inputs = self.assets / "input"
        self.previews = self.root / "data" / "media_previews"
        for path in (self.generated, self.inputs, self.previews):
            path.mkdir(parents=True, exist_ok=True)
        self.patches = [
            patch.object(main, "ASSETS_DIR", str(self.assets)),
            patch.object(main, "OUTPUT_OUTPUT_DIR", str(self.generated)),
            patch.object(main, "OUTPUT_DIR", str(self.generated)),
            patch.object(main, "MEDIA_PREVIEW_DIR", str(self.previews)),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def write_png(self, name="warm.png"):
        path = self.generated / name
        Image.new("RGB", (96, 64), "red").save(path)
        return path, f"/assets/output/{name}"

    def test_warmup_widths_match_canvas_preview_sizes(self):
        self.assertEqual(main.MEDIA_PREVIEW_WARMUP_WIDTHS, (512, 768))

    def test_media_preview_endpoint_keeps_url_and_width_contract(self):
        params = inspect.signature(main.media_preview).parameters
        self.assertIn("url", params)
        self.assertIn("w", params)
        self.assertEqual(params["w"].default, 512)

    async def test_build_and_warmup_create_512_and_768_cache(self):
        path, url = self.write_png()
        out_path, media_type = main.build_media_preview_file(str(path), 512)
        self.assertTrue(Path(out_path).exists())
        self.assertIn(media_type, {"image/webp", "image/png"})

        task = main.schedule_media_preview_warmup(url)
        self.assertIsNotNone(task)
        await task

        for width in main.MEDIA_PREVIEW_WARMUP_WIDTHS:
            webp, png = main.media_preview_cache_paths(str(path), width)
            self.assertTrue(Path(webp).exists() or Path(png).exists(), f"missing preview cache for w={width}")

    def test_warmup_skips_non_media(self):
        path = self.generated / "notes.txt"
        path.write_text("hello", encoding="utf-8")
        self.assertFalse(main.can_warmup_media_preview(str(path)))
        self.assertIsNone(main.schedule_media_preview_warmup("/assets/output/notes.txt"))

    def write_fake_mp4(self, name="clip.mp4"):
        path = self.generated / name
        path.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 128)
        return path, f"/assets/output/{name}"

    def test_video_placeholder_is_small_webp(self):
        payload = main.video_preview_placeholder_bytes()
        self.assertTrue(payload.startswith(b"RIFF"))
        self.assertIn(b"WEBP", payload[:16])
        self.assertLess(len(payload), 4096)

    def test_video_preview_without_ffmpeg_raises(self):
        path, _url = self.write_fake_mp4()
        with patch.object(main.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg"):
                main.generate_video_preview_image(str(path), 512)

    async def test_video_preview_without_ffmpeg_returns_image_not_video(self):
        from fastapi.responses import FileResponse

        path, url = self.write_fake_mp4()
        with patch.object(main.shutil, "which", return_value=None):
            resp = await main.media_preview(url, 512)
        self.assertFalse(isinstance(resp, FileResponse))
        self.assertEqual(resp.media_type, "image/webp")
        self.assertEqual(resp.headers.get("cache-control"), "no-store")
        body = resp.body
        self.assertTrue(body.startswith(b"RIFF"))
        self.assertIn(b"WEBP", body[:16])
        self.assertLess(len(body), 4096)
        webp, png = main.media_preview_cache_paths(str(path), 512)
        self.assertFalse(Path(webp).exists())
        self.assertFalse(Path(png).exists())


if __name__ == "__main__":
    unittest.main()
