"""Durable queue: claiming, retries, fatal failures, and cancellation."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from core import queue, states
from core.models import Job, Project


class QueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("eng", password="pw")
        self.project = Project.objects.create(name="q", owner=self.user)

    def make_job(self, key="k", kind="", payload=None):
        return Job.objects.create(
            project=self.project,
            idempotency_key=key,
            input_revision="rev-1",
            task_kind=kind,
            task_payload=dict(payload or {}),
        )

    def test_enqueue_marks_job_claimable(self):
        job = self.make_job("k1")
        queue.enqueue(job, "image_design", {"brief": "rover"})
        job.refresh_from_db()
        self.assertEqual(job.task_kind, "image_design")
        self.assertEqual(job.task_payload["brief"], "rover")
        claimed = queue.claim_next("w1")
        self.assertEqual(claimed.id, job.id)

    def test_claim_is_exclusive_and_oldest_first(self):
        first = self.make_job("k1", "image_design")
        second = self.make_job("k2", "image_design")
        self.assertEqual(queue.claim_next("w1").id, first.id)
        # Same worker cannot claim the same job twice; the next claim moves on.
        self.assertEqual(queue.claim_next("w1").id, second.id)
        self.assertIsNone(queue.claim_next("w2"))

    def test_completed_handler_clears_claim(self):
        job = self.make_job("k1", "test_task")
        claimed = queue.claim_next("w1")
        with patch("core.queue._handlers", return_value={"test_task": lambda j: None}):
            self.assertEqual(queue.run_claimed(claimed), "completed")
        job.refresh_from_db()
        self.assertEqual(job.claimed_by, "")
        self.assertEqual(job.attempts, 1)

    def test_transient_failure_retries_then_fails(self):
        job = self.make_job("k1", "test_task")
        def boom(handler_job):
            raise RuntimeError("provider down")
        with patch("core.queue._handlers", return_value={"test_task": boom}):
            for _ in range(queue.MAX_ATTEMPTS - 1):
                claimed = queue.claim_next("w1")
                self.assertEqual(queue.run_claimed(claimed), "retrying")
            claimed = queue.claim_next("w1")
            self.assertEqual(queue.run_claimed(claimed), "failed")
        job.refresh_from_db()
        self.assertEqual(job.state, states.FAILED)
        self.assertTrue(job.artifacts.filter(kind="report").exists())

    def test_fatal_failure_skips_retry(self):
        job = self.make_job("k1", "test_task")
        def bad_input(handler_job):
            raise queue.FatalTaskError("bad dimensions")
        claimed = queue.claim_next("w1")
        with patch("core.queue._handlers", return_value={"test_task": bad_input}):
            self.assertEqual(queue.run_claimed(claimed), "failed")
        job.refresh_from_db()
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.state, states.FAILED)

    def test_cancel_requested_job_is_not_run(self):
        job = self.make_job("k1", "test_task")
        job.cancel_requested = True
        job.save(update_fields=["cancel_requested"])
        self.assertIsNone(queue.claim_next("w1"))
        # A claim that slips through before the flag still cancels cleanly.
        job.cancel_requested = False
        job.save(update_fields=["cancel_requested"])
        claimed = queue.claim_next("w1")
        claimed.cancel_requested = True
        claimed.save(update_fields=["cancel_requested"])
        with patch("core.queue._handlers", return_value={"test_task": lambda j: None}):
            self.assertEqual(queue.run_claimed(claimed), "cancelled")
        job.refresh_from_db()
        self.assertEqual(job.state, states.CANCELLED)

    def test_unknown_task_kind_fails_loudly(self):
        job = self.make_job("k1", "nope")
        claimed = queue.claim_next("w1")
        with patch("core.queue._handlers", return_value={}):
            self.assertEqual(queue.run_claimed(claimed), "failed")
        self.assertTrue(job.artifacts.filter(kind="report").exists())

    def test_reap_stale_jobs(self):
        job = self.make_job("k1", "image_design")
        job.state = states.SPECIFYING
        job.claimed_by = "dead_worker"
        job.save()
        # With timeout 0, it reaps immediately
        reaped = queue.reap_stale_jobs(timeout_s=0)
        self.assertEqual(reaped, 1)
        job.refresh_from_db()
        self.assertEqual(job.state, states.FAILED)
        self.assertEqual(job.claimed_by, "")
