"""参考图格式链路：落盘时按真实字节命名，出站时把上游不收的格式转掉。

背景：上游 /v1/videos 只接受 jpeg/png/webp/bmp/tiff/gif/heic/heif。
旧逻辑按 content_type 猜扩展名，会把 HEIC/AVIF/TIFF 一律存成 .png，
名实不符的文件送上去，上游要跑完一次异步计费任务才报 unsupported image format。
"""
import base64
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

from fastapi import HTTPException
from PIL import Image, features

import main


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
HEIC_HEAD = b"\x00\x00\x00\x20ftypheic" + b"\x00" * 32
AVIF_HEAD = b"\x00\x00\x00\x20ftypavif" + b"\x00" * 32
MP4_HEAD = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 32
JPEG_HEAD = b"\xff\xd8\xff\xe0" + b"\x00" * 12


def avif_supported():
    try:
        return bool(features.check("avif"))
    except Exception:
        return False


class SniffImageExtTests(unittest.TestCase):
    def test_recognizes_every_format_the_upstream_talks_about(self):
        cases = {
            ".png": PNG_1PX,
            ".jpg": JPEG_HEAD,
            ".webp": b"RIFF\x00\x00\x00\x00WEBPVP8 ",
            ".gif": b"GIF89a" + b"\x00" * 10,
            ".bmp": b"BM" + b"\x00" * 14,
            ".tiff": b"II\x2a\x00" + b"\x00" * 12,
            ".avif": AVIF_HEAD,
            ".heic": HEIC_HEAD,
            ".heif": b"\x00\x00\x00\x20ftypmif1" + b"\x00" * 32,
            ".svg": b'<svg xmlns="http://www.w3.org/2000/svg"/>',
        }
        for expected, head in cases.items():
            self.assertEqual(main._sniff_image_ext_bytes(head), expected, expected)

    def test_mp4_and_garbage_are_not_mistaken_for_images(self):
        self.assertIsNone(main._sniff_image_ext_bytes(MP4_HEAD))
        self.assertIsNone(main._sniff_image_ext_bytes(b"\x00\x01\x02\x03"))
        self.assertIsNone(main._sniff_image_ext_bytes(b""))

    def test_migration_scope_did_not_widen_with_the_new_formats(self):
        """迁移会重命名历史文件，画布 JSON 里的旧 URL 会因此失效——范围必须保持原样。"""
        self.assertEqual(main._MIGRATABLE_IMAGE_EXTS, {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})


class LocalUploadKindExtTests(unittest.TestCase):
    def test_real_bytes_win_over_a_lying_filename_and_content_type(self):
        self.assertEqual(main._local_upload_kind_ext("photo.png", "image/png", HEIC_HEAD), ("image", ".heic"))
        self.assertEqual(main._local_upload_kind_ext("shot", "image/png", AVIF_HEAD), ("image", ".avif"))

    def test_without_content_the_old_guessing_behaviour_is_unchanged(self):
        self.assertEqual(main._local_upload_kind_ext("photo.png", "image/png"), ("image", ".png"))
        self.assertEqual(main._local_upload_kind_ext("shot", "image/avif"), ("image", ".png"))
        self.assertEqual(main._local_upload_kind_ext("a.jpeg", "image/jpeg"), ("image", ".jpeg"))

    def test_jpeg_and_jpg_are_not_rewritten_into_each_other(self):
        self.assertEqual(main._local_upload_kind_ext("a.jpeg", "image/jpeg", JPEG_HEAD), ("image", ".jpeg"))
        self.assertEqual(main._local_upload_kind_ext("a.jpg", "image/jpeg", JPEG_HEAD), ("image", ".jpg"))

    def test_video_and_audio_branches_are_untouched(self):
        self.assertEqual(main._local_upload_kind_ext("a.mp4", "video/mp4", MP4_HEAD), ("video", ".mp4"))
        self.assertEqual(main._local_upload_kind_ext("a.mp3", "audio/mpeg", b""), ("audio", ".mp3"))
        self.assertEqual(main._local_upload_kind_ext("a.txt", "text/plain", b"hello"), (None, ".txt"))


class AiImageExtTests(unittest.TestCase):
    def test_declared_mime_is_only_a_fallback(self):
        self.assertEqual(main._ai_image_ext(AVIF_HEAD, "image/png"), ".avif")
        self.assertEqual(main._ai_image_ext(PNG_1PX, "image/jpeg"), ".png")
        self.assertEqual(main._ai_image_ext(b"unrecognized", "image/webp"), ".webp")
        self.assertEqual(main._ai_image_ext(b"", ""), ".png")


class UpstreamSafeImageRefTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return f"/output/{name}"

    def _patched_paths(self):
        def fake_output_file_from_url(url):
            path = os.path.join(self.tmp, os.path.basename(str(url)))
            return path if os.path.exists(path) else ""

        return mock.patch.multiple(
            main,
            output_file_from_url=fake_output_file_from_url,
            output_path_for=lambda name, category="output": os.path.join(self.tmp, name),
            output_url_for=lambda name, category="output": f"/output/{name}",
        )

    def test_formats_the_upstream_accepts_are_passed_through_untouched(self):
        url = self._write("ref.png", PNG_1PX)
        with self._patched_paths():
            self.assertEqual(main.upstream_safe_image_ref(url), url)

    def test_heic_is_passed_through_because_the_upstream_accepts_it(self):
        url = self._write("ref.heic", HEIC_HEAD)
        with self._patched_paths():
            self.assertEqual(main.upstream_safe_image_ref(url), url)

    def test_video_reference_is_left_alone(self):
        url = self._write("clip.mp4", MP4_HEAD)
        with self._patched_paths():
            self.assertEqual(main.upstream_safe_image_ref(url), url)

    def test_svg_fails_loudly_instead_of_burning_an_upstream_task(self):
        url = self._write("ref.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        with self._patched_paths():
            with self.assertRaises(HTTPException) as ctx:
                main.upstream_safe_image_ref(url)
        self.assertEqual(ctx.exception.status_code, 400)

    @unittest.skipUnless(avif_supported(), "本机 Pillow 未启用 AVIF")
    def test_avif_is_converted_to_png_before_going_out(self):
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), (255, 0, 0)).save(buf, format="AVIF")
        url = self._write("ref.avif", buf.getvalue())
        with self._patched_paths():
            converted_url = main.upstream_safe_image_ref(url)
        self.assertNotEqual(converted_url, url)
        self.assertTrue(converted_url.endswith(".png"))
        with Image.open(os.path.join(self.tmp, os.path.basename(converted_url))) as img:
            self.assertEqual(img.format, "PNG")
            self.assertEqual(img.size, (4, 4))

    @unittest.skipUnless(avif_supported(), "本机 Pillow 未启用 AVIF")
    def test_conversion_is_reused_instead_of_rerun_for_the_same_file(self):
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), (0, 128, 255)).save(buf, format="AVIF")
        url = self._write("ref.avif", buf.getvalue())
        with self._patched_paths():
            first = main.upstream_safe_image_ref(url)
            second = main.upstream_safe_image_ref(url)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
