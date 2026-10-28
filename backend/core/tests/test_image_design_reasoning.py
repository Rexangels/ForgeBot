"""Two-stage image-to-spec reasoning, grounding, and bounded revision checks."""

import math
from unittest.mock import patch

from django.test import SimpleTestCase

from core.image_design import (
    _component_visuals,
    _is_leg_component,
    apply_revision_edits,
    generate_image_robot_urdf,
    ground_image_design,
    propose_image_robot,
    propose_single_revision,
    validate_image_design,
)


def _design():
    return {
        "schema_version": "image-robot-concept/v1",
        "title": "Test rover",
        "robot_type": "rover",
        "design_rationale": "fixture",
        "estimated_dimensions_m": {"width": 0.4, "depth": 0.3, "height": 0.5},
        "components": [
            {"id": "base", "name": "Base", "role": "chassis", "parent_id": None, "geometry": "box", "dimensions_m": [0.4, 0.3, 0.1], "position_m": [0, 0, 0.2], "color": "#287c72"},
            {"id": "left_wheel", "name": "Left wheel", "role": "drive wheel", "parent_id": "base", "geometry": "cylinder", "dimensions_m": [0.1, 0.1, 0.05], "position_m": [0, 0.2, 0.05], "color": "#343a40", "symmetry_group": "axle_pair"},
            {"id": "right_wheel", "name": "Right wheel", "role": "drive wheel", "parent_id": "base", "geometry": "cylinder", "dimensions_m": [0.12, 0.12, 0.05], "position_m": [0, -0.2, 0.05], "color": "#343a40", "symmetry_group": "axle_pair"},
        ],
        "assumptions": [],
        "manufacturing_notes": [],
    }


def _observation():
    return {
        "schema_version": "image-robot-observation/v1",
        "scene_summary": "Small rover with deck and two wheels.",
        "observed_assemblies": [
            {"label": "deck", "appearance": "dark rectangular deck", "image_region": {"x_min": 0.2, "y_min": 0.2, "x_max": 0.8, "y_max": 0.5}, "visibility": "visible", "confidence": 0.9, "functional_hint": "chassis"},
            {"label": "wheels", "appearance": "two side wheels", "image_region": {"x_min": 0.2, "y_min": 0.5, "x_max": 0.8, "y_max": 0.8}, "visibility": "visible", "confidence": 0.8, "functional_hint": "locomotion"},
        ],
    }


class ImageReasoningTests(SimpleTestCase):
    def test_legacy_spec_gets_reasoning_defaults(self):
        design = validate_image_design(_design(), 0.5)
        base = design["components"][0]
        self.assertEqual(base["functional_category"], "structure")
        self.assertEqual(base["orientation_rpy"], [0.0, 0.0, 0.0])
        self.assertEqual(base["visible_vs_inferred"], "visible")
        self.assertEqual(base["confidence"], 0.5)
        self.assertIsNone(base["symmetry_group"])

    def test_grounding_averages_symmetry_group(self):
        design = validate_image_design(_design(), 0.5)
        grounded, report = ground_image_design(design, 0.5)
        left = next(part for part in grounded["components"] if part["id"] == "left_wheel")
        right = next(part for part in grounded["components"] if part["id"] == "right_wheel")
        self.assertEqual(left["dimensions_m"], right["dimensions_m"])
        self.assertEqual(report["status"], "grounded")

    @patch("core.image_design.generate_multi_image_structured_json")
    @patch("core.image_design.generate_image_structured_json")
    def test_two_stage_pipeline_embeds_observation_and_grounding(self, observe, design_call):
        observe.return_value = _observation()
        design_call.return_value = _design()
        design = propose_image_robot(b"\xff\xd8\xff" + b"0" * 64, "image/jpeg", "surveillance rover", 0.5, "")
        self.assertIn("visual_observation", design)
        self.assertIn("grounding", design)
        self.assertEqual(observe.call_count, 1)
        self.assertEqual(design_call.call_count, 1)
        for part in design["components"]:
            self.assertIn("functional_category", part)
            self.assertIn("confidence", part)

    @patch("core.image_design.generate_multi_image_structured_json")
    @patch("core.image_design.generate_image_structured_json")
    def test_pipeline_falls_back_when_observation_fails(self, observe, design_call):
        from core.meta_model import MetaModelError

        observe.side_effect = MetaModelError("vision unavailable")
        design_call.return_value = _design()
        design = propose_image_robot(b"\xff\xd8\xff" + b"0" * 64, "image/jpeg", "surveillance rover", 0.5, "")
        self.assertNotIn("visual_observation", design)
        self.assertIn("grounding", design)

    @patch("core.image_design.generate_multi_image_structured_json")
    @patch("core.image_design.generate_image_structured_json")
    def test_multi_view_pipeline_labels_each_view(self, observe, design_call):
        from core.image_design import propose_image_robot_from_views

        first, second = _observation(), _observation()
        observe.side_effect = [first, second]
        design_call.return_value = _design()
        views = [
            {"image": b"\xff\xd8\xff" + b"1" * 64, "content_type": "image/jpeg", "view": "front"},
            {"image": b"\xff\xd8\xff" + b"2" * 64, "content_type": "image/jpeg", "view": "side"},
        ]
        design = propose_image_robot_from_views(views, "surveillance rover", 0.5, "")
        self.assertEqual([item["view"] for item in design["visual_observation"]], ["front", "side"])
        sent_images = design_call.call_args.kwargs["images"]
        self.assertEqual([label for label, _ in sent_images], ["front", "side"])

    def test_reference_view_limits_are_enforced(self):
        from core.image_design import propose_image_robot_from_views

        with self.assertRaises(ValueError):
            propose_image_robot_from_views([], "brief", 0.5, "")
        many = [{"image": b"\xff\xd8\xff0", "content_type": "image/jpeg", "view": "other"} for _ in range(4)]
        with self.assertRaises(ValueError):
            propose_image_robot_from_views(many, "brief", 0.5, "")
        with self.assertRaises(ValueError):
            propose_image_robot_from_views([{"image": b"nope", "content_type": "image/jpeg", "view": "front"}], "brief", 0.5, "")

class SubAssemblyTests(SimpleTestCase):
    def _part(self, part_id, parent_id, sub_assembly=None):
        return {"id": part_id, "name": part_id.replace("_", " "), "role": "leg segment", "parent_id": parent_id, "geometry": "box", "dimensions_m": [0.05, 0.05, 0.1], "position_m": [0, 0, 0.1], "color": "#287c72", "sub_assembly": sub_assembly}

    def _big_design(self, count):
        components = [self._part("base", None)]
        for index in range(1, count):
            components.append(self._part(f"seg_{index}", "base", sub_assembly="leg_fl" if index % 2 else None))
        return {
            "schema_version": "image-robot-concept/v1", "title": "Big", "robot_type": "walker", "design_rationale": "fixture",
            "estimated_dimensions_m": {"width": 0.4, "depth": 0.4, "height": 0.3},
            "components": components, "assumptions": [], "manufacturing_notes": [],
        }

    def test_twenty_components_validate_with_groups(self):
        design = validate_image_design(self._big_design(20), 0.3)
        grouped = [part for part in design["components"] if part["sub_assembly"] == "leg_fl"]
        self.assertEqual(len(grouped), 10)
        self.assertIsNone(design["components"][0]["sub_assembly"])

    def test_twenty_one_components_rejected(self):
        from core.image_design import MAX_COMPONENTS
        from core.meta_model import MetaModelError

        self.assertEqual(MAX_COMPONENTS, 20)
        with self.assertRaises(MetaModelError):
            validate_image_design(self._big_design(21), 0.3)

    def test_bad_group_name_defaults_to_none(self):
        dirty = self._part("x1", "base", sub_assembly="not a group!")
        design = validate_image_design({**self._big_design(3), "components": [self._part("base", None), dirty, self._part("x2", "base")]}, 0.3)
        self.assertIsNone(next(part for part in design["components"] if part["id"] == "x1")["sub_assembly"])

    def test_assembly_check_accepts_twenty(self):
        from core.image_design import validate_cad_assembly

        report = validate_cad_assembly(self._big_design(20))
        self.assertNotIn("component_count", [item.get("code") for item in report["findings"]])


class LegVisualTests(SimpleTestCase):
    def _leg(self, leg_id="leg_fl", lateral=0.2):
        return {
            "id": leg_id, "name": "Front left leg", "role": "walking leg",
            "parent_id": "base", "geometry": "box",
            "dimensions_m": [0.05, 0.05, 0.24], "position_m": [0.16, lateral, -0.12],
            "color": "#343a40",
        }

    def test_leg_detection_by_id_name_and_category(self):
        self.assertTrue(_is_leg_component(self._leg()))
        self.assertTrue(_is_leg_component({"id": "femur_l", "name": "Limb", "role": "support", "functional_category": "walking"}))
        self.assertFalse(_is_leg_component({"id": "base", "name": "Base plate", "role": "chassis"}))
        self.assertFalse(_is_leg_component({"id": "wheel_left", "name": "Drive Wheel Left", "role": "traction wheel", "functional_category": "locomotion", "geometry": "cylinder"}))
        self.assertFalse(_is_leg_component({"id": "motor_left", "name": "Gear Motor Left", "role": "drive motor", "functional_category": "locomotion", "geometry": "box"}))

    def test_leg_expands_to_femur_tibia_and_foot(self):
        visuals = _component_visuals(self._leg(), [0, 0, 0.1])
        self.assertEqual(len(visuals), 3)
        femur, tibia, foot = visuals
        # Femur splays outward with a non-zero roll; tibia drops vertically.
        self.assertNotAlmostEqual(femur["rpy"][0], 0.0)
        # The rolled femur must point outward and downward, not up.
        self.assertLess(math.cos(femur["rpy"][0]), 0)
        self.assertGreater(-math.sin(femur["rpy"][0]), 0)
        self.assertEqual(tibia["rpy"], [0, 0, 0])
        self.assertEqual(foot["geometry"], "box")
        # Femur sits in the upper half of the envelope, foot at the bottom.
        self.assertGreater(femur["origin_m"][2], 0)
        self.assertLessEqual(foot["origin_m"][2], -0.1)

    def test_mirrored_legs_splay_opposite_sides(self):
        left = _component_visuals(self._leg("leg_fl", 0.2), [0, 0, 0.1])[0]
        right = _component_visuals(self._leg("leg_fr", -0.2), [0, 0, 0.1])[0]
        self.assertAlmostEqual(left["rpy"][0], -right["rpy"][0])
        self.assertGreater(left["origin_m"][1], 0)
        self.assertLess(right["origin_m"][1], 0)

    def test_non_leg_components_keep_single_visual(self):
        block = {"id": "ballast", "name": "Ballast block", "role": "dead weight", "parent_id": "base", "geometry": "box", "dimensions_m": [0.12, 0.08, 0.04], "position_m": [0, 0, 0.08], "color": "#287c72"}
        self.assertEqual(len(_component_visuals(block, None)), 1)

    def test_detail_branches_expand_known_parts(self):
        cases = {
            "motor": ("Drive motor", "gear motor", 3),
            "pcb": ("Flight controller", "control board", 12),
            "battery": ("Battery pack", "power supply", 5),
            "standoff": ("Spacer post", "deck spacer", 3),
            "joint": ("Hinge", "lid hinge", 4),
            "propeller": ("Rotor", "lift propeller", 3),
            "antenna": ("Antenna", "telemetry whip", 3),
            "foot": ("Foot pad", "landing foot", 2),
            "sonar": ("Sonar", "ultrasonic ranger", 3),
        }
        for part_id, (name, role, expected) in cases.items():
            with self.subTest(part=part_id):
                component = {"id": part_id, "name": name, "role": role, "parent_id": "base", "geometry": "box", "dimensions_m": [0.1, 0.08, 0.05], "position_m": [0, 0, 0.1], "color": "#287c72"}
                self.assertEqual(len(_component_visuals(component, None)), expected)

    def test_urdf_renders_three_visuals_per_leg(self):
        design = {
            "schema_version": "image-robot-concept/v1",
            "title": "Walker", "robot_type": "hexapod", "design_rationale": "fixture",
            "estimated_dimensions_m": {"width": 0.4, "depth": 0.4, "height": 0.3},
            "components": [
                {"id": "base", "name": "Base", "role": "chassis", "parent_id": None, "geometry": "box", "dimensions_m": [0.3, 0.25, 0.05], "position_m": [0, 0, 0.1], "color": "#287c72"},
                self._leg("leg_fl", 0.2),
                self._leg("leg_fr", -0.2),
            ],
            "assumptions": [], "manufacturing_notes": [],
        }
        urdf = generate_image_robot_urdf(validate_image_design(design, 0.3))
        self.assertEqual(urdf.count("visual_leg_fl_"), 3)
        self.assertEqual(urdf.count("visual_leg_fr_"), 3)

    @patch("core.image_design.generate_structured_json")
    def test_bounded_revision_requires_human_review(self, generate):
        generate.return_value = {
            "schema_version": "image-robot-revision/v1",
            "rationale": "Separate the deck and wheel solids.",
            "edits": [{"component_id": "left_wheel", "position_m": [0, 0.24, 0.05], "reason": "Clear the deck edge."}],
        }
        design = validate_image_design(_design(), 0.225)
        findings = [{"component_ids": ["base", "left_wheel"], "intersection_volume_mm3": 900.0, "status": "intersects_review_required"}]
        proposal = propose_single_revision(design, findings, "Fix the overlap")
        self.assertTrue(proposal["requires_human_review"])
        self.assertEqual(len(proposal["edits"]), 1)
        updated = apply_revision_edits(design, proposal, 0.225)
        left = next(part for part in updated["components"] if part["id"] == "left_wheel")
        self.assertAlmostEqual(left["position_m"][1], 0.24)
