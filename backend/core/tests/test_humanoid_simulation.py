"""Humanoid physics: URDF conversion plus stand, reach, and stepping scenarios."""

import json

from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase

from core.humanoid import generate_humanoid_urdf, validate_humanoid_design
from core.humanoid_simulation import _run_reach, _run_stand, _run_step, run_humanoid_simulation, urdf_to_mjcf
from core.models import Job, Project


def humanoid_spec():
    return {
        "schema_version": "research-humanoid/v1",
        "robot_class": "research_humanoid",
        "name": "Fixture biped",
        "summary": "Mid-range template dimensions for simulation tests.",
        "height_m": 1.7,
        "mass_kg": 45,
        "payload_per_arm_kg": 1.5,
        "battery_energy_wh": 500,
        "geometry": {
            "pelvis_width_m": 0.26, "pelvis_height_m": 0.17, "shoulder_width_m": 0.4,
            "torso_length_m": 0.45, "neck_height_m": 0.09, "head_height_m": 0.22,
            "upper_arm_length_m": 0.32, "forearm_length_m": 0.29, "hand_length_m": 0.13,
            "thigh_length_m": 0.36, "shin_length_m": 0.36, "foot_length_m": 0.24,
            "foot_height_m": 0.11,
        },
        "sensor_suite": ["head_rgbd_camera", "imu", "tactile_hands"],
        "compute_platform": "fixture board",
        "bom": [{"component": f"part-{index}", "purpose": "test fixture", "availability": "unverified"} for index in range(8)],
        "assumptions": ["template inertias"],
        "open_questions": [],
    }


class HumanoidConversionTests(SimpleTestCase):
    def test_fixture_spec_validates(self):
        self.assertEqual(validate_humanoid_design(humanoid_spec()), [])

    def test_converter_covers_every_hinge(self):
        mjcf, hinges = urdf_to_mjcf(generate_humanoid_urdf(humanoid_spec()))
        # 31 body joints plus 20 finger joints per hand.
        self.assertEqual(len(hinges), 71)
        self.assertIn('<freejoint name="root_free"/>', mjcf)
        self.assertIn("<actuator>", mjcf)

    def test_garbage_urdf_rejected(self):
        from core.humanoid_simulation import HumanoidSimulationError

        with self.assertRaises(HumanoidSimulationError):
            urdf_to_mjcf("<robot></robot>")


class HumanoidEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("eng", password="pw")
        self.client = Client()
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="hum", owner=self.user)
        self.job = Job.objects.create(project=self.project, idempotency_key="h1", input_revision="rev-1")
        self.job.artifacts.create(kind="spec", schema_version="research-humanoid/v1", content_hash="h", body=json.dumps(humanoid_spec()))

    def test_simulate_humanoid_stores_report(self):
        response = self.client.post(f"/api/v1/jobs/{self.job.id}/simulate-humanoid")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn("acceptance_checks", body)
        self.assertTrue(self.job.artifacts.filter(kind="humanoid_sim").exists())

    def test_simulate_humanoid_rejects_arm_spec(self):
        spec = self.job.artifacts.get(kind="spec")
        arm = json.loads(spec.body)
        arm["robot_class"] = "tabletop_robot_arm"
        spec.body = json.dumps(arm)
        spec.save(update_fields=["body"])
        response = self.client.post(f"/api/v1/jobs/{self.job.id}/simulate-humanoid")
        self.assertEqual(response.status_code, 409)


class HumanoidScenarioTests(SimpleTestCase):
    def test_stand_holds_with_feet_grounded(self):
        stand = _run_stand(humanoid_spec())
        self.assertTrue(stand["passed"], stand)

    def test_reach_is_symmetric_and_forward(self):
        reach = _run_reach(humanoid_spec())
        self.assertTrue(reach["passed"], reach)

    def test_step_attempt_records_honest_outcome(self):
        step = _run_step(humanoid_spec(), duration_s=4.0)
        self.assertIn("distance_m", step)
        self.assertIn("fell_at_s", step)
        # Whatever it measures must be a finite number, pass or finding.
        self.assertTrue(step["distance_m"] == step["distance_m"])

    def test_step_walks_1m_without_fall(self):
        step = _run_step(humanoid_spec())
        self.assertIsNone(step["fell_at_s"], step)
        self.assertGreaterEqual(step["distance_m"], 1.0, step)
        self.assertTrue(step["passed"], step)

    def test_full_report_marks_walk_by_evidence(self):
        result = run_humanoid_simulation(humanoid_spec())
        self.assertEqual(result["schema_version"], "forgebot-humanoid-simulation/v1")
        self.assertEqual(result["engine"], "MuJoCo")
        self.assertIn("walk_1m_without_fall", result["acceptance_checks"])
        self.assertTrue(result["scenarios"]["stand"]["passed"])
        self.assertEqual(result["passed"], all(result["acceptance_checks"].values()))
