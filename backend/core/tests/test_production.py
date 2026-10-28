"""Production posture: quotas, audit actor, ops summary."""

import json

from django.contrib.auth.models import User
from django.test import Client, TestCase

from core import states
from core.models import Job, Project
from core.quotas import QuotaExceeded, check_job_quotas


class QuotaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("eng", password="pw")
        self.project = Project.objects.create(name="q", owner=self.user)

    def test_active_project_cap(self):
        for index in range(10):
            Job.objects.create(project=self.project, idempotency_key=f"k{index}", input_revision="rev-1")
        with self.assertRaises(QuotaExceeded):
            check_job_quotas(self.project, self.user)

    def test_terminal_jobs_free_quota(self):
        for index in range(10):
            job = Job.objects.create(project=self.project, idempotency_key=f"k{index}", input_revision="rev-1")
            job.transition(states.FAILED)
        check_job_quotas(self.project, self.user)

    def test_daily_user_cap(self):
        from django.test import override_settings

        for index in range(10):
            Job.objects.create(project=self.project, idempotency_key=f"k{index}", input_revision="rev-1")
            Job.objects.filter(idempotency_key=f"k{index}").update(state=states.FAILED)
        with override_settings(FORGEBOT_MAX_JOBS_PER_USER_PER_DAY=10):
            with self.assertRaises(QuotaExceeded):
                check_job_quotas(self.project, self.user)

    def test_create_job_enforces_and_replays_bypass(self):
        client = Client()
        client.force_login(self.user)
        for index in range(10):
            Job.objects.create(project=self.project, idempotency_key=f"fill{index}", input_revision="rev-1")
        refused = client.post(
            f"/api/v1/projects/{self.project.id}/jobs",
            data=json.dumps({"idempotency_key": "one-more"}),
            content_type="application/json",
        )
        self.assertEqual(refused.status_code, 409)
        replay = client.post(
            f"/api/v1/projects/{self.project.id}/jobs",
            data=json.dumps({"idempotency_key": "fill0"}),
            content_type="application/json",
        )
        self.assertEqual(replay.status_code, 200)


class AuditAndOpsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("eng", password="pw")
        self.client = Client()
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="ops", owner=self.user)
        self.job = Job.objects.create(project=self.project, idempotency_key="o1", input_revision="rev-1")

    def test_cancel_records_actor(self):
        response = self.client.post(f"/api/v1/jobs/{self.job.id}/cancel")
        self.assertEqual(response.status_code, 200)
        event = self.job.events.filter(actor="eng").first()
        self.assertIsNotNone(event)

    def test_ops_summary_reports_queue_and_audits(self):
        self.client.post(f"/api/v1/jobs/{self.job.id}/cancel")
        response = self.client.get("/api/v1/ops/summary")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("jobs_by_state", body)
        self.assertIn("queue_depth", body)
        self.assertIn("quotas", body)
        actors = {item["actor"] for item in body["recent_human_actions"]}
        self.assertIn("eng", actors)
