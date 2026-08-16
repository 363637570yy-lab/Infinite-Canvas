import asyncio
import unittest

from fastapi import HTTPException

import h3_protocol as h3
import main


def run(coro):
    return asyncio.run(coro)


H3_MODELS_RESPONSE = {
    "object": "list",
    "data": [
        {
            "id": "minimax-h3",
            "object": "model",
            "owned_by": "video-api",
            "supported_endpoint_types": ["openai-video"],
            "description": "async /v1/videos",
        }
    ],
}


def video_payload(**kwargs):
    base = {
        "prompt": "电影感镜头缓慢推进",
        "provider_id": "h3-local",
        "model": "minimax-h3",
        "duration": 5,
        "aspect_ratio": "16:9",
        "resolution": "480p",
    }
    base.update(kwargs)
    return main.CanvasVideoRequest(**base)


class H3RoutingTests(unittest.TestCase):
    def test_protocol_and_urls_are_registered(self):
        provider = {"id": "h3-local", "protocol": main.H3_PROTOCOL}

        self.assertIn(main.H3_PROTOCOL, main.SUPPORTED_PROVIDER_PROTOCOLS)
        self.assertTrue(main.is_h3_provider(provider))
        self.assertTrue(main.is_h3_route(provider, "minimax-h3"))
        self.assertFalse(main.is_megabyai_route(provider, "minimax-h3"))
        self.assertEqual(
            main.video_submit_url_candidates(provider, "http://127.0.0.1:8088", "minimax-h3"),
            ["http://127.0.0.1:8088/v1/videos"],
        )
        self.assertEqual(
            main.video_task_url_candidates(provider, "http://127.0.0.1:8088/v1", "video_abc", "", "minimax-h3"),
            ["http://127.0.0.1:8088/v1/videos/video_abc"],
        )
        self.assertEqual(main.canvas_video_task_type(provider, "minimax-h3"), "h3-video")

    def test_models_use_upstream_capabilities_not_names(self):
        grouped, ids = main.parse_upstream_models(H3_MODELS_RESPONSE, main.H3_PROTOCOL)
        self.assertEqual(ids, ["minimax-h3"])
        self.assertIn("minimax-h3", grouped["video"])
        self.assertEqual(h3.classify_h3_model_entry({}, "minimax-h3"), "chat")


class H3RequestTests(unittest.TestCase):
    def test_canvas_fields_only(self):
        body, images = h3.build_h3_video_request(video_payload(), "minimax-h3")
        self.assertEqual(
            body,
            {
                "model": "minimax-h3",
                "prompt": "电影感镜头缓慢推进",
                "seconds": 5,
                "size": "480p",
                "aspect_ratio": "16:9",
            },
        )
        self.assertEqual(images, [])
        for wrong in ("steps", "ycnodes", "duration", "resolution"):
            self.assertNotIn(wrong, body)

    def test_duration_snaps_and_rejects_portrait(self):
        body, _ = h3.build_h3_video_request(video_payload(duration=8, resolution="720p"), "minimax-h3")
        self.assertEqual(body["seconds"], 8)
        self.assertEqual(body["size"], "720p")
        with self.assertRaises(HTTPException):
            h3.build_h3_video_request(video_payload(duration=4), "minimax-h3")
        with self.assertRaises(HTTPException):
            h3.build_h3_video_request(video_payload(aspect_ratio="9:16"), "minimax-h3")

    def test_rejects_extra_media(self):
        with self.assertRaises(HTTPException):
            h3.build_h3_video_request(video_payload(videos=["http://example/a.mp4"]), "minimax-h3")
        with self.assertRaises(HTTPException):
            h3.build_h3_video_request(
                video_payload(images=[{"url": "a.png"}, {"url": "b.png"}]),
                "minimax-h3",
            )


if __name__ == "__main__":
    unittest.main()
