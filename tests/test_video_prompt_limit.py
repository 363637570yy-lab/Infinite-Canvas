import unittest

from pydantic import ValidationError

import main


class VideoPromptLimitTests(unittest.TestCase):
    def test_shared_video_gate_is_20000(self):
        self.assertEqual(main.VIDEO_PROMPT_MAX_LENGTH, 20000)
        main.CanvasVideoRequest(prompt="x" * 20000)
        with self.assertRaises(ValidationError):
            main.CanvasVideoRequest(prompt="x" * 20001)
