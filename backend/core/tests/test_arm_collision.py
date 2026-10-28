"""Deterministic arm collision prediction: geometry, poses, and obstacles."""

from django.test import SimpleTestCase, TestCase

from core.arm_collision import (
    check_arm_collisions,
    check_arm_path,
    link_segments,
    point_box_distance,
    segment_box_clearance,
    segment_segment_distance,
    workcell_obstacles,
)


def _spec():
    return {
        "geometry": {
            "base_diameter_m": 0.12,
            "base_height_m": 0.06,
            "upper_arm_length_m": 0.18,
            "forearm_length_m": 0.18,
            "wrist_length_m": 0.08,
        }
    }


class SegmentMathTests(SimpleTestCase):
    def test_parallel_segments_keep_distance(self):
        self.assertAlmostEqual(
            segment_segment_distance((0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)), 1.0
        )

    def test_crossing_segments_touch(self):
        self.assertAlmostEqual(
            segment_segment_distance((0, 0, 0), (1, 0, 0), (0.5, -1, 0), (0.5, 1, 0)), 0.0
        )

    def test_point_box_signed_distance(self):
        self.assertAlmostEqual(point_box_distance((0.2, 0, 0), (0, 0, 0), (0.1, 0.1, 0.1)), 0.1)
        self.assertLess(point_box_distance((0, 0, 0), (0, 0, 0), (0.1, 0.1, 0.1)), 0)

    def test_segment_box_clearance_accounts_radius(self):
        clearance = segment_box_clearance((0.3, 0, 0), (0.5, 0, 0), 0.02, (0, 0, 0), (0.1, 0.1, 0.1))
        self.assertAlmostEqual(clearance, 0.18, places=3)


class ArmCollisionTests(SimpleTestCase):
    def test_home_pose_is_self_clear(self):
        joints = {"base_yaw": 0, "shoulder_pitch": 0, "elbow_pitch": 0, "wrist_pitch": 0}
        segments = link_segments(_spec(), joints)
        self.assertEqual(set(segments), {"base_column", "upper_arm", "forearm", "wrist"})
        self.assertEqual(check_arm_collisions(_spec(), joints), [])

    def test_folded_elbow_self_collides(self):
        # Elbow folded +149 degrees tucks the forearm into the base column.
        joints = {"base_yaw": 0, "shoulder_pitch": 0, "elbow_pitch": 2.6, "wrist_pitch": -2.6}
        findings = check_arm_collisions(_spec(), joints)
        self.assertTrue(any(item["type"] == "self_collision" and set(item["links"]) == {"base_column", "forearm"} for item in findings))

    def test_tool_inside_obstacle_is_flagged(self):
        joints = {"base_yaw": 0, "shoulder_pitch": 0, "elbow_pitch": 0, "wrist_pitch": 0}
        box = {"name": "probe", "center_m": (0.44, 0, 0.13), "half_m": (0.05, 0.05, 0.05)}
        findings = check_arm_collisions(_spec(), joints, [box])
        self.assertTrue(any(item["type"] == "obstacle_collision" and item["obstacle"] == "probe" for item in findings))

    def test_distant_obstacle_stays_clear(self):
        joints = {"base_yaw": 0, "shoulder_pitch": 0, "elbow_pitch": 0, "wrist_pitch": 0}
        box = {"name": "far", "center_m": (0.44, 0.5, 0.13), "half_m": (0.05, 0.05, 0.05)}
        self.assertEqual(check_arm_collisions(_spec(), joints, [box]), [])

    def test_path_reports_first_blocking_waypoint(self):
        home = {"base_yaw": 0, "shoulder_pitch": 0, "elbow_pitch": 0, "wrist_pitch": 0}
        folded = {"base_yaw": 0, "shoulder_pitch": 0, "elbow_pitch": 2.6, "wrist_pitch": -2.6}
        findings = check_arm_path(_spec(), [home, home, folded])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["waypoint_index"], 2)

    def test_workcell_obstacles_default_to_payload_only(self):
        obstacles = workcell_obstacles((0.28, 0.0, 0.055), (0.2, 0.18, 0.055))
        self.assertEqual({item["name"] for item in obstacles}, {"pick_payload", "place_payload"})

    def test_configured_bin_adds_walls(self):
        obstacles = workcell_obstacles((0.28, 0.0, 0.055), (0.2, 0.18, 0.055), bin_half=(0.1, 0.1, 0.05))
        names = {item["name"] for item in obstacles}
        self.assertTrue({"pick_payload", "place_payload", "bin_wall_x+", "bin_wall_y-"} <= names)


class CollisionPhysicsAgreementTests(TestCase):
    """The predictor must agree with MuJoCo contacts on the default task."""

    def test_default_task_predicts_clear_and_sim_shows_no_arm_contacts(self):
        from core.arm_simulation import run_arm_simulation
        from core.robot_arm import ARM_SPEC_SCHEMA_VERSION, JOINT_NAMES
        from core.tests.test_arm_simulation import arm_spec

        spec = arm_spec()
        self.assertEqual(spec["schema_version"], ARM_SPEC_SCHEMA_VERSION)
        self.assertEqual(len(JOINT_NAMES), 4)
        result = run_arm_simulation(spec)
        self.assertTrue(result["collision_prediction"]["predicted_clear"], result["collision_prediction"]["findings"])
        arm_contacts = [
            entry for entry in result["metrics"]["unexpected_contacts"]
            if " and " in entry
            and all(part.endswith("_geom") for part in entry.split(" and "))
            and "payload_geom" not in entry
            and "table" not in entry
            and "finger_geom" not in entry
        ]
        self.assertEqual(arm_contacts, [], result["metrics"]["observed_contacts"])
