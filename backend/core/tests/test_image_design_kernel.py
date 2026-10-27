"""CAD-kernel export and evidence-aware sketch-proposal checks."""

from unittest.mock import patch

from django.test import SimpleTestCase

from core.image_design import generate_cad_package, propose_component_sketch_edit


def small_design():
    return {
        "schema_version": "image-robot-concept/v1",
        "title": "Small rover",
        "robot_type": "rover",
        "design_rationale": "Test fixture",
        "estimated_dimensions_m": {"width": 0.3, "depth": 0.2, "height": 0.15},
        "components": [
            {"id": "base", "name": "Base plate", "role": "chassis", "parent_id": None, "geometry": "box", "dimensions_m": [0.2, 0.16, 0.03], "position_m": [0, 0, 0.04], "color": "#287c72"},
            {"id": "left_wheel", "name": "Left wheel", "role": "drive wheel", "parent_id": "base", "geometry": "cylinder", "dimensions_m": [0.06, 0.06, 0.025], "position_m": [0, 0.11, 0.04], "color": "#343a40"},
            {"id": "right_wheel", "name": "Right wheel", "role": "drive wheel", "parent_id": "base", "geometry": "cylinder", "dimensions_m": [0.06, 0.06, 0.025], "position_m": [0, -0.11, 0.04], "color": "#343a40"},
        ],
        "assumptions": [],
        "manufacturing_notes": [],
    }


class ImageDesignKernelTests(SimpleTestCase):
    def test_freecad_macro_contains_exact_shape_and_interference_checks(self):
        package = generate_cad_package(small_design())
        macro = package["files"]["freecad/build_step.FCMacro"]
        self.assertIn("shape.isValid()", macro)
        self.assertIn("first_shape.common(second_shape)", macro)
        self.assertIn("forgebot-freecad-kernel-validation/v1", macro)

    @patch("core.image_design.generate_structured_json")
    def test_ai_sketch_proposal_receives_verified_kernel_findings(self, generate):
        generate.return_value = {"dimensions_m": [0.18, 0.15, 0.03], "rationale": "Reduce the deck envelope.", "review_notes": "Rebuild and check mounts."}
        finding = {"component_ids": ["base", "left_wheel"], "intersection_volume_mm3": 1250.0, "status": "intersects_review_required"}
        proposal = propose_component_sketch_edit(small_design(), "base", "Reduce the overlap", [finding])
        self.assertEqual(proposal["dimensions_m"], [0.18, 0.15, 0.03])
        prompt = generate.call_args.kwargs["user_prompt"]
        self.assertIn("1250.0", prompt)
        self.assertIn("left_wheel", prompt)
        self.assertTrue(proposal["requires_human_review"])
