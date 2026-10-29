"""MuJoCo physics and API acceptance checks for the tabletop-arm MVP."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from core import states
from core.arm_simulation import ArmSimulationError, run_arm_simulation
from core.artifacts import store_artifact
from core.models import Job, Project
from core.robot_arm import ARM_SPEC_SCHEMA_VERSION, JOINT_NAMES, apply_arm_repair, propose_arm_repair


def arm_spec():
    return {
        "schema_version": ARM_SPEC_SCHEMA_VERSION,
        "robot_class": "tabletop_robot_arm",
        "name": "Acceptance arm",
        "summary": "A small tabletop pick-and-place arm.",
        "geometry": {
            "base_diameter_m": 0.12,
            "base_height_m": 0.06,
            "upper_arm_length_m": 0.18,
            "forearm_length_m": 0.18,
            "wrist_length_m": 0.08,
        },
        "masses": {
            "base_kg": 1.0,
            "shoulder_kg": 0.2,
            "upper_arm_kg": 0.25,
            "forearm_kg": 0.2,
            "wrist_kg": 0.1,
            "tool_kg": 0.1,
        },
        "payload_kg": 0.05,
        "joint_limits": [
            {"name": name, "lower_deg": low, "upper_deg": high, "effort_nm": 10, "velocity_rad_s": 1}
            for name, low, high in zip(JOINT_NAMES, (-180, -135, -150, -135), (180, 135, 150, 135))
        ],
        "gripper": {"type": "parallel_jaw", "jaw_opening_m": 0.05, "grip_force_n": 5},
        "bom": [{"component": f"part-{index}", "purpose": "test fixture", "availability": "unverified"} for index in range(4)],
        "assumptions": [],
        "open_questions": [],
    }


class MuJoCoArmSimulationTests(TestCase):
    def test_contact_pick_and_place_completes_with_repeatable_metrics(self):
        first = run_arm_simulation(arm_spec())
        second = run_arm_simulation(arm_spec())
        self.assertEqual(first["engine"], "MuJoCo")
        self.assertEqual(first["timestep_s"], 0.002)
        self.assertTrue(first["deterministic"])
        self.assertTrue(first["passed"], first["metrics"])
        self.assertTrue(first["metrics"]["grasp_contact_seen"])
        self.assertTrue(first["metrics"]["payload_lifted"])
        self.assertTrue(first["metrics"]["payload_released"])
        self.assertTrue(first["metrics"]["payload_resting_on_table"])
        self.assertTrue(all(first["acceptance_checks"].values()))
        self.assertIn("no weld or ideal grasp constraint", first["assumptions"][2])
        self.assertLessEqual(first["metrics"]["goal_error_m"], 0.05)
        self.assertEqual(first["metrics"]["unexpected_contacts"], [])
        self.assertEqual(first["metrics"]["goal_error_m"], second["metrics"]["goal_error_m"])
        self.assertGreater(len(first["trajectory"]), 2)

    def test_vision_seeded_pick_runs_and_records_seed(self):
        seed = {"x": 0.28, "y": 0.0, "z": 0.055, "uncertainty_m": 0.004, "depth_source": "measured"}
        result = run_arm_simulation(arm_spec(), perceived_target=seed)
        self.assertTrue(result["passed"], result["metrics"])
        self.assertEqual(result["task"]["vision_seed"]["depth_source"], "measured")
        self.assertEqual(result["task"]["pick_target_m"], [0.28, 0.0, 0.055])

    def test_vision_seed_beyond_gate_is_refused(self):
        seed = {"x": 0.28, "y": 0.0, "uncertainty_m": 0.05, "depth_source": "assumed_plane_40mm"}
        with self.assertRaises(ArmSimulationError):
            run_arm_simulation(arm_spec(), perceived_target=seed)

    def test_vision_seed_needs_uncertainty(self):
        with self.assertRaises(ArmSimulationError):
            run_arm_simulation(arm_spec(), perceived_target={"x": 0.28, "y": 0.0})

    def test_controller_tuning_returns_a_recorded_search(self):
        result = run_arm_simulation(arm_spec(), train=True)
        training = result["controller"]["training"]
        self.assertEqual(training["status"], "completed")
        self.assertEqual(training["method"], "deterministic coordinate search over position-servo gains")
        self.assertEqual(len(training["history"]), 9)
        self.assertEqual(len(training["selected_gains"]), 4)
        self.assertIn("not reinforcement learning", training["notice"])

    def test_unreachable_target_is_rejected_before_simulation(self):
        with self.assertRaises(ArmSimulationError):
            run_arm_simulation(arm_spec(), pick_target=(0.7, 0, 0.05))

    def test_reviewed_repair_changes_one_parameter_and_preserves_original(self):
        spec = arm_spec()
        proposal = {"parameter": "joint_effort_nm", "joint_name": "shoulder_pitch", "value": 12.0, "rationale": "Improve simulated joint tracking.", "review_notes": "Rerun the task and verify motor sizing."}
        updated = apply_arm_repair(spec, proposal)
        self.assertEqual(spec["joint_limits"][1]["effort_nm"], 10)
        self.assertEqual(updated["joint_limits"][1]["effort_nm"], 12)
        self.assertTrue(updated["design_history"][-1]["requires_resimulation"])
        with self.assertRaises(ValueError):
            apply_arm_repair(spec, {**proposal, "value": 100})

    @patch("core.robot_arm.generate_structured_json")
    def test_ai_repair_uses_only_current_simulation_evidence(self, generate):
        generate.return_value = {"parameter": "joint_effort_nm", "joint_name": "shoulder_pitch", "value": 12.0, "rationale": "Increase available joint effort.", "review_notes": "Verify the physical motor separately."}
        simulation = {"schema_version": "forgebot-arm-simulation/v1", "passed": False, "metrics": {"goal_error_m": 0.16}}
        proposal = propose_arm_repair(arm_spec(), simulation)
        self.assertTrue(proposal["requires_human_review"])
        self.assertEqual(proposal["parameter"], "joint_effort_nm")
        self.assertIn("0.16", generate.call_args.kwargs["user_prompt"])
        with self.assertRaises(ValueError):
            propose_arm_repair(arm_spec(), {"passed": False})

    def test_authenticated_endpoint_persists_simulation_artifact(self):
        user = User.objects.create_user("sim-user", password="pw")
        project = Project.objects.create(name="simulation", owner=user)
        job = Job.objects.create(project=project, state=states.SUCCEEDED, idempotency_key="arm-sim")
        store_artifact(job, "spec", __import__("json").dumps(arm_spec()), ARM_SPEC_SCHEMA_VERSION)
        self.client.force_login(user)
        response = self.client.post(f"/api/v1/jobs/{job.id}/simulate-arm", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        artifact = job.artifacts.get(id=response.json()["artifact_id"])
        self.assertEqual(artifact.kind, "sim")
        self.assertEqual(artifact.schema_version, "sim/mujoco-arm/v1")

    @patch("core.arm_simulation.run_arm_simulation")
    def test_endpoint_forwards_perceived_target(self, run):
        run.return_value = {"passed": True, "engine": "MuJoCo", "engine_version": "test"}
        user = User.objects.create_user("seed-user", password="pw")
        project = Project.objects.create(name="seed", owner=user)
        job = Job.objects.create(project=project, state=states.SUCCEEDED, idempotency_key="arm-seed")
        store_artifact(job, "spec", __import__("json").dumps(arm_spec()), ARM_SPEC_SCHEMA_VERSION)
        self.client.force_login(user)
        payload = {"perceived_target": {"x": 0.28, "y": 0.0, "uncertainty_m": 0.004, "depth_source": "measured"}}
        response = self.client.post(
            f"/api/v1/jobs/{job.id}/simulate-arm",
            data=__import__("json").dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(run.call_args.kwargs["perceived_target"], payload["perceived_target"])

    def test_project_benchmark_runs_same_task_for_recent_arm_designs(self):
        import json
        user = User.objects.create_user("benchmark-user", password="pw")
        project = Project.objects.create(name="benchmark", owner=user)
        jobs = []
        for index in range(2):
            job = Job.objects.create(project=project, state=states.SUCCEEDED, idempotency_key=f"benchmark-{index}")
            store_artifact(job, "spec", json.dumps(arm_spec()), ARM_SPEC_SCHEMA_VERSION)
            jobs.append(job)
        self.client.force_login(user)
        response = self.client.post(f"/api/v1/jobs/{jobs[0].id}/benchmark-arm-designs", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        record = response.json()["benchmark"]
        self.assertEqual(record["design_count"], 2)
        self.assertEqual({result["job_id"] for result in record["results"]}, {job.id for job in jobs})
        self.assertTrue(all(result["acceptance_checks"]["payload_released"] for result in record["results"]))
        artifact = jobs[0].artifacts.get(id=response.json()["artifact_id"])
        self.assertEqual(artifact.kind, "sim_benchmark")

    def test_wrong_state_and_wrong_owner_are_rejected(self):
        user = User.objects.create_user("sim-user", password="pw")
        project = Project.objects.create(name="simulation", owner=user)
        job = Job.objects.create(project=project, idempotency_key="waiting")
        self.client.force_login(user)
        self.assertEqual(self.client.post(f"/api/v1/jobs/{job.id}/simulate-arm", data="{}", content_type="application/json").status_code, 409)
        other = User.objects.create_user("other", password="pw")
        self.client.force_login(other)
        self.assertEqual(self.client.post(f"/api/v1/jobs/{job.id}/simulate-arm", data="{}", content_type="application/json").status_code, 404)

    def test_apply_repair_rebuilds_artifacts_and_invalidates_simulation(self):
        import json
        user = User.objects.create_user("repair-user", password="pw")
        project = Project.objects.create(name="repair", owner=user)
        job = Job.objects.create(project=project, state=states.SUCCEEDED, idempotency_key="repair")
        spec_artifact = store_artifact(job, "spec", json.dumps(arm_spec()), ARM_SPEC_SCHEMA_VERSION)
        store_artifact(job, "sim", json.dumps({"schema_version": "forgebot-arm-simulation/v1", "spec_artifact_id": spec_artifact.id, "passed": False}), "sim/mujoco-arm/v1")
        proposal = {"parameter": "joint_effort_nm", "joint_name": "shoulder_pitch", "value": 12.0, "rationale": "Reduce tracking error.", "review_notes": "Check the motor rating.", "requires_human_review": True}
        proposal_artifact = store_artifact(job, "repair_proposal", json.dumps(proposal), "arm-repair-proposal/v1")
        proposal["proposal_artifact_id"] = proposal_artifact.id
        self.client.force_login(user)
        response = self.client.post(f"/api/v1/jobs/{job.id}/apply-arm-repair", data=json.dumps(proposal), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        updated = json.loads(job.artifacts.filter(kind="spec").order_by("-id").first().body)
        stale = json.loads(job.artifacts.filter(kind="sim").order_by("-id").first().body)
        self.assertEqual(updated["joint_limits"][1]["effort_nm"], 12)
        self.assertEqual(stale["status"], "stale")
        self.assertTrue(job.artifacts.filter(kind="urdf").exists())
        self.assertTrue(job.artifacts.filter(kind="code").exists())
