"""Maintained tests for the synchronous demo pipeline."""

from django.contrib.auth.models import User
from django.test import TestCase

from core import states
from core.demo import run_from_approval, run_to_review
from core.models import Job, Project

MISSION = {"task": "ferry parts", "units": "si", "environment": "flat floor"}


class DemoPipelineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("eng", password="pw")
        self.project = Project.objects.create(name="demo", owner=self.user)

    def make_job(self, key="k1"):
        return Job.objects.create(
            project=self.project, idempotency_key=key, input_revision="rev-1"
        )

    def test_demo_reaches_awaiting_review_with_proposal(self):
        job = self.make_job()
        self.assertEqual(run_to_review(job, MISSION), [])
        job.refresh_from_db()
        self.assertEqual(job.state, states.AWAITING_REVIEW)
        kinds = set(job.artifacts.values_list("kind", flat=True))
        self.assertTrue({"mission", "spec", "bom"} <= kinds)

    def test_bad_mission_fails_with_report(self):
        job = self.make_job()
        errors = run_to_review(job, {"task": "", "units": "furlongs"})
        self.assertTrue(errors)
        job.refresh_from_db()
        self.assertEqual(job.state, states.FAILED)
        self.assertTrue(job.artifacts.filter(kind="report").exists())

    def test_approval_completes_rover_run(self):
        job = self.make_job()
        run_to_review(job, MISSION)
        summary = run_from_approval(job)
        self.assertTrue(summary["passed"])
        job.refresh_from_db()
        self.assertEqual(job.state, states.SUCCEEDED)
        kinds = set(job.artifacts.values_list("kind", flat=True))
        for expected in ("urdf", "code", "report", "sim", "manifest"):
            self.assertIn(expected, kinds)
        events = list(job.events.values_list("state", flat=True))
        for state in (
            states.SPECIFYING,
            states.AWAITING_REVIEW,
            states.GENERATING,
            states.VALIDATING,
            states.SIMULATING,
            states.SUCCEEDED,
        ):
            self.assertIn(state, events)

    def test_approve_view_rejects_wrong_state(self):
        self.client.force_login(self.user)
        job = self.make_job()
        res = self.client.post(f"/api/v1/jobs/{job.id}/approve", {})
        self.assertEqual(res.status_code, 409)

    def test_login_accepts_vite_dev_origin(self):
        # Regression: the browser sends Origin http://localhost:5173 through
        # the Vite proxy; Django must not 403 the login POST.
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        client.get("/api/v1/auth/csrf")
        token = client.cookies["csrftoken"].value
        res = client.post(
            "/api/v1/auth/login",
            {"username": "eng", "password": "pw"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_ORIGIN="http://localhost:5173",
        )
        self.assertEqual(res.status_code, 200)
        res = client.get("/api/v1/auth/me")
        self.assertEqual(res.json()["username"], "eng")

    def test_demo_endpoints_run_full_review_loop(self):
        self.client.force_login(self.user)
        res = self.client.post(
            f"/api/v1/projects/{self.project.id}/demo-jobs",
            {"idempotency_key": "web-1", "mission": MISSION},
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        job_id = res.json()["id"]
        res = self.client.post(f"/api/v1/jobs/{job_id}/approve", {})
        self.assertEqual(res.json()["state"], states.SUCCEEDED)
