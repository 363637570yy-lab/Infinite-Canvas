import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


def big_canvas(canvas_id, marker):
    """够大的画布，保证一次写入跨多个缓冲块 —— 原地截断写才会有可观测的残缺窗口。"""
    return {
        "id": canvas_id,
        "title": "atomic",
        "marker": marker,
        "logs": [],
        "connections": [],
        "viewport": {"x": 0, "y": 0, "scale": 1},
        "updated_at": 0,
        "nodes": [
            {"id": f"node-{i}", "type": "smart-image", "promptDraftText": "x" * 400}
            for i in range(2000)
        ],
    }


class CanvasAtomicWriteTests(unittest.TestCase):
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

    def temp_files(self):
        return sorted(p.name for p in self.canvases.iterdir() if p.name.endswith(".tmp"))

    def test_write_is_readable_and_leaves_no_temp_file(self):
        path = self.canvases / "plain.json"
        main.atomic_write_json(str(path), {"id": "plain", "value": 1})

        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"id": "plain", "value": 1})
        self.assertEqual(self.temp_files(), [])

    def test_failed_write_keeps_previous_content_intact(self):
        """旧的 open('w') 写法在这里会把目标文件毁成残缺 JSON。"""
        path = self.canvases / "keep.json"
        main.atomic_write_json(str(path), big_canvas("keep", "before"))
        before = path.read_text(encoding="utf-8")

        def half_written(data, fp, **kwargs):
            fp.write('{"id": "keep", "nodes": [{"id": "node-0"')
            raise RuntimeError("boom")

        with patch.object(main.json, "dump", side_effect=half_written):
            with self.assertRaises(RuntimeError):
                main.atomic_write_json(str(path), big_canvas("keep", "after"))

        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(json.loads(before)["marker"], "before")
        self.assertEqual(self.temp_files(), [])

    def test_concurrent_reads_never_see_a_partial_canvas(self):
        """复现线上 500：读画布的同时高频自动保存，读侧不得拿到残缺 JSON。"""
        canvas_id = "race"
        main.save_canvas(big_canvas(canvas_id, "seed"))
        errors = []
        stop = threading.Event()

        def writer():
            try:
                for i in range(40):
                    main.save_canvas(big_canvas(canvas_id, f"round-{i}"))
            except Exception as exc:  # 写侧异常同样算失败
                errors.append(f"writer: {exc!r}")
            finally:
                stop.set()

        def reader():
            while not stop.is_set():
                try:
                    loaded = main.load_canvas(canvas_id)
                    if not loaded.get("marker"):
                        errors.append("reader: canvas missing marker")
                except Exception as exc:
                    errors.append(f"reader: {exc!r}")

        threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertEqual(errors, [])
        self.assertEqual(self.temp_files(), [])

    def test_listing_ignores_leftover_temp_files(self):
        main.save_canvas(big_canvas("listed", "ok"))
        (self.canvases / "listed.json.tmp").write_text('{"id": "listed"', encoding="utf-8")

        records = main.iter_canvas_records()

        self.assertEqual([item["id"] for item in records], ["listed"])

    def test_listing_does_not_purge_expired_trash(self):
        canvas = {
            "id": "old-trash",
            "title": "gone",
            "nodes": [],
            "deleted_at": 1,
            "updated_at": 1,
        }
        main.atomic_write_json(str(self.canvases / "old-trash.json"), canvas)
        main._canvas_record_cache.update({"dir": None, "sig": None, "files": {}, "live": [], "trash": []})

        self.assertEqual(main.iter_canvas_records(), [])
        self.assertEqual([item["id"] for item in main.iter_canvas_records(include_deleted=True)], ["old-trash"])
        self.assertTrue((self.canvases / "old-trash.json").exists())

    def test_listing_cache_tracks_title_after_save(self):
        main.save_canvas({"id": "named", "title": "one", "nodes": []})
        self.assertEqual(main.list_canvases()[0]["title"], "one")
        main.save_canvas({"id": "named", "title": "two", "nodes": []})
        self.assertEqual(main.list_canvases()[0]["title"], "two")

    def test_meta_uses_index_without_loading_full_canvas(self):
        main.save_canvas({"id": "meta1", "title": "hello", "icon": "star", "kind": "classic", "nodes": [{"id": "n1"}]})
        with patch.object(main, "load_canvas", side_effect=AssertionError("meta must not open the full canvas")):
            rec = main.canvas_meta_payload("meta1")
        self.assertEqual(rec["id"], "meta1")
        self.assertEqual(rec["title"], "hello")
        self.assertEqual(rec["icon"], "star")
        self.assertEqual(rec["kind"], "classic")

    def test_deleted_canvas_meta_is_404(self):
        from fastapi import HTTPException
        canvas = {"id": "gone-meta", "title": "x", "nodes": [], "deleted_at": 1, "updated_at": 1}
        main.atomic_write_json(str(self.canvases / "gone-meta.json"), canvas)
        main._canvas_record_cache.update({"dir": None, "sig": None, "files": {}, "live": [], "trash": []})
        with self.assertRaises(HTTPException) as ctx:
            main.canvas_meta_payload("gone-meta")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_get_canvas_endpoint_still_returns_nodes(self):
        import asyncio
        main.save_canvas({"id": "full1", "title": "body", "nodes": [{"id": "n1", "type": "prompt"}]})
        data = asyncio.run(main.get_canvas("full1"))
        self.assertEqual(data["canvas"]["title"], "body")
        self.assertEqual(data["canvas"]["nodes"][0]["id"], "n1")

    def test_projects_write_is_atomic(self):
        main.save_projects([{"id": "p1", "name": "项目一"}])

        self.assertEqual(
            json.loads(self.projects_path.read_text(encoding="utf-8")),
            {"projects": [{"id": "p1", "name": "项目一"}]},
        )
        self.assertEqual(
            sorted(p.name for p in self.data.iterdir() if p.name.endswith(".tmp")),
            [],
        )


if __name__ == "__main__":
    unittest.main()
