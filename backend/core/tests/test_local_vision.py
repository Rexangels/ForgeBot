"""Local-only reference images: stdlib probing plus the text-only design path."""

import struct
from unittest.mock import patch

from django.test import SimpleTestCase

from core.image_design import propose_image_robot_from_views
from core.local_vision import LocalVisionError, describe_view, probe_image


def _png(width, height):
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00" + struct.pack(">I", 0) + b"IEND" + b"\x00\x00\x00\x00"


def _jpeg(width, height):
    sof = b"\xff\xc0" + struct.pack(">H", 11) + struct.pack(">BHH", 8, height, width) + b"\x03\x01\x11\x00"
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9 + sof + b"\xff\xd9"


def _gif(width, height):
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 7


def _bmp(width, height):
    return b"BM" + struct.pack("<I", 54) + b"\x00" * 12 + struct.pack("<ii", width, height) + b"\x00" * 8


def _webp_vp8(width, height):
    frame = b"\x00\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", width, height) + b"\x00" * 20
    return b"RIFF" + struct.pack("<I", 4 + 8 + len(frame)) + b"WEBP" + b"VP8 " + struct.pack("<I", len(frame)) + frame


def _design():
    return {
        "schema_version": "image-robot-concept/v1",
        "title": "Local rover",
        "robot_type": "rover",
        "design_rationale": "fixture",
        "estimated_dimensions_m": {"width": 0.4, "depth": 0.3, "height": 0.5},
        "components": [
            {"id": "base", "name": "Base", "role": "chassis", "parent_id": None, "geometry": "box", "dimensions_m": [0.4, 0.3, 0.1], "position_m": [0, 0, 0.2], "color": "#287c72"},
            {"id": "left_wheel", "name": "Left wheel", "role": "drive wheel", "parent_id": "base", "geometry": "cylinder", "dimensions_m": [0.1, 0.1, 0.05], "position_m": [0, 0.2, 0.05], "color": "#343a40"},
            {"id": "right_wheel", "name": "Right wheel", "role": "drive wheel", "parent_id": "base", "geometry": "cylinder", "dimensions_m": [0.1, 0.1, 0.05], "position_m": [0, -0.2, 0.05], "color": "#343a40"},
        ],
        "assumptions": [],
        "manufacturing_notes": [],
    }


class LocalProbeTests(SimpleTestCase):
    def test_png_probe(self):
        self.assertEqual(probe_image(_png(640, 480))["width_px"], 640)
        self.assertEqual(probe_image(_png(640, 480))["height_px"], 480)

    def test_jpeg_probe(self):
        probe = probe_image(_jpeg(320, 200))
        self.assertEqual((probe["format"], probe["width_px"], probe["height_px"]), ("jpeg", 320, 200))

    def test_gif_and_bmp_probes(self):
        self.assertEqual(probe_image(_gif(100, 50))["format"], "gif")
        self.assertEqual(probe_image(_bmp(100, 50))["height_px"], 50)

    def test_webp_vp8_probe(self):
        probe = probe_image(_webp_vp8(550, 368))
        self.assertEqual((probe["format"], probe["width_px"]), ("webp", 550))

    def test_garbage_rejected(self):
        with self.assertRaises(LocalVisionError):
            probe_image(b"not an image at all..............")
        with self.assertRaises(LocalVisionError):
            probe_image(b"")

    def test_describe_view_names_locality(self):
        line = describe_view("front", {"format": "png", "width_px": 8, "height_px": 8, "bytes": 90}, 0)
        self.assertIn("no pixels", line)


class LocalOnlyPipelineTests(SimpleTestCase):
    @patch("core.image_design.generate_image_structured_json")
    @patch("core.image_design.generate_multi_image_structured_json")
    @patch("core.image_design.generate_structured_json")
    def test_local_only_sends_no_pixels(self, text_call, multi_call, observe_call):
        multi_call.side_effect = AssertionError("pixels must not upload in local-only mode")
        observe_call.side_effect = AssertionError("stage 1 must not run in local-only mode")
        text_call.return_value = _design()
        views = [
            {"image": _png(64, 48), "content_type": "image/png", "view": "front"},
            {"image": _jpeg(32, 24), "content_type": "image/jpeg", "view": "side"},
        ]
        result = propose_image_robot_from_views(views, "small warehouse rover", 0.5, local_only=True)
        self.assertTrue(result["pixels_local_only"])
        self.assertEqual(len(result["local_probes"]), 2)
        self.assertNotIn("visual_observation", result)
        prompt = text_call.call_args.kwargs["user_prompt"]
        self.assertNotIn("base64", prompt)
        self.assertIn("kept on this machine", prompt)
        self.assertTrue(any("never left this machine" in text for text in result["assumptions"]))

    def test_local_only_rejects_unreadable_bytes(self):
        views = [{"image": b"junk" * 20, "content_type": "image/png", "view": "front"}]
        with self.assertRaises(ValueError):
            with patch("core.image_design.generate_structured_json"):
                propose_image_robot_from_views(views, "brief", 0.5, local_only=True)
