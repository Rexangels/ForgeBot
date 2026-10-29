"""Tests for closed-loop vision-in-the-loop multi-view rendering and critique."""

import io
from unittest.mock import patch
from PIL import Image

from django.test import SimpleTestCase

from core.visual_critique import (
    apply_visual_critique_refinements,
    render_cad_multiview,
    run_vision_in_the_loop_critique,
)


def _mock_critique():
    return {
        "schema_version": "image-robot-visual-critique/v1",
        "visual_match_score": 0.92,
        "passed_verification": True,
        "critique_summary": "CAD assembly matches the reference silhouette closely.",
        "symmetry_assessment": "Four-corner suspension and mirrored wheel pairs are verified.",
        "stance_and_mounting_assessment": "Main body is centered properly over the wheelbase.",
        "component_adjustments": [
            {
                "component_id": "head_cam",
                "issue": "Camera is slightly low relative to reference dome.",
                "suggested_action": "Elevate camera by 10mm.",
                "delta_position_m": [0.0, 0.0, 0.01],
                "delta_dimensions_m": [0.0, 0.0, 0.0],
            }
        ],
        "missing_parts_identified": [
            {
                "name": "Antenna mast",
                "role": "telemetry antenna",
                "functional_category": "communication",
                "likely_position_m": [-0.1, 0.05, 0.35],
                "likely_dimensions_m": [0.01, 0.01, 0.12],
                "parent_id": "chassis",
                "geometry": "cylinder",
                "color": "#334155",
            }
        ],
    }


class VisualCritiqueTests(SimpleTestCase):
    def setUp(self):
        self.design = {
            "schema_version": "image-robot-concept/v1",
            "title": "Patrol Rover",
            "robot_type": "rover",
            "design_rationale": "fixture",
            "estimated_dimensions_m": {"width": 0.4, "depth": 0.3, "height": 0.4},
            "components": [
                {
                    "id": "chassis",
                    "name": "Chassis base",
                    "role": "chassis",
                    "parent_id": None,
                    "geometry": "box",
                    "dimensions_m": [0.35, 0.28, 0.08],
                    "position_m": [0.0, 0.0, 0.12],
                    "color": "#287c72",
                },
                {
                    "id": "wheel_fl",
                    "name": "Wheel FL",
                    "role": "front wheel",
                    "parent_id": "chassis",
                    "geometry": "cylinder",
                    "dimensions_m": [0.12, 0.12, 0.05],
                    "position_m": [0.12, 0.15, 0.06],
                    "color": "#1e293b",
                },
                {
                    "id": "wheel_fr",
                    "name": "Wheel FR",
                    "role": "front wheel",
                    "parent_id": "chassis",
                    "geometry": "cylinder",
                    "dimensions_m": [0.12, 0.12, 0.05],
                    "position_m": [0.12, -0.15, 0.06],
                    "color": "#1e293b",
                },
                {
                    "id": "head_cam",
                    "name": "Camera head",
                    "role": "sensing",
                    "parent_id": "chassis",
                    "geometry": "box",
                    "dimensions_m": [0.08, 0.08, 0.06],
                    "position_m": [0.05, 0.0, 0.22],
                    "color": "#0284c7",
                },
            ],
            "assumptions": [],
            "manufacturing_notes": [],
        }

    def test_render_cad_multiview_generates_valid_png(self):
        png_bytes = render_cad_multiview(self.design["components"], 512, 512)
        self.assertIsInstance(png_bytes, bytes)
        self.assertGreater(len(png_bytes), 1000)

        img = Image.open(io.BytesIO(png_bytes))
        self.assertEqual(img.format, "PNG")
        self.assertEqual(img.size, (512, 512))

    @patch("core.visual_critique.generate_multi_image_structured_json")
    def test_run_vision_in_the_loop_critique(self, mock_generate):
        mock_generate.return_value = _mock_critique()
        ref_views = [{"image": b"\x89PNG\r\n\x1a\n" + b"0" * 32, "content_type": "image/png", "view": "front"}]
        critique = run_vision_in_the_loop_critique(ref_views, self.design, "Patrol rover", 0.4)

        self.assertEqual(critique["visual_match_score"], 0.92)
        self.assertTrue(critique["passed_verification"])
        self.assertIn("multiview_render_base64", critique)

        # Ensure multi-image call sent both the reference photo and the CAD render
        self.assertEqual(mock_generate.call_count, 1)
        _, kwargs = mock_generate.call_args
        images = kwargs["images"]
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0][0], "user_photo_front")
        self.assertEqual(images[1][0], "cad_3d_render_multiview")

    def test_apply_visual_critique_refinements(self):
        critique = _mock_critique()
        refined, notes = apply_visual_critique_refinements(self.design, critique, 0.4)

        part_ids = [p["id"] for p in refined["components"]]
        self.assertIn("antenna_mast", part_ids)

        antenna = next(p for p in refined["components"] if p["id"] == "antenna_mast")
        self.assertEqual(antenna["geometry"], "cylinder")
        self.assertEqual(antenna["parent_id"], "chassis")

        self.assertIn("visual_critique", refined)
        self.assertEqual(refined["visual_critique"]["visual_match_score"], 0.92)
        self.assertTrue(any("adjustment" in n.lower() for n in notes))
        self.assertTrue(any("missing component" in n.lower() for n in notes))
