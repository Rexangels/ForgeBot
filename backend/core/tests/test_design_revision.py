"""Revision endpoints: propose from findings, apply after human approval."""

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Job, Project


def _gappy_design():
    return {
        "schema_version": "image-robot-concept/v1",
        "robot_class": "image_robot_design",
        "title": "Mast",
        "robot_type": "tower",
        "design_rationale": "fixture",
        "estimated_dimensions_m": {"width": 0.3, "depth": 0.3, "height": 0.6},
        "components": [
            {"id": "base", "name": "Base", "role": "chassis", "parent_id": None, "geometry": "box", "dimensions_m": [0.3, 0.3, 0.05], "position_m": [0, 0, 0.025], "color": "#287c72"},
            {"id": "mast", "name": "Mast", "role": "sensor mast", "parent_id": "base", "geometry": "box", "dimensions_m": [0.05, 0.05, 0.2], "position_m": [0, 0, 0.5], "color": "#287c72"},
            {"id": "tip", "name": "Tip", "role": "beacon", "parent_id": "mast", "geometry": "sphere", "dimensions_m": [0.04, 0.04, 0.04], "position_m": [0, 0, 0.62], "color": "#287c72"},
        ],
        "assumptions": [],
        "manufacturing_notes": [],
    }


class DesignRevisionEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("eng", password="pw")
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="rev", owner=self.user)
        self.job = Job.objects.create(project=self.project, idempotency_key="r1", input_revision="rev-1")
        self.job.artifacts.create(kind="spec", schema_version="image-robot-concept/v1", content_hash="s", body=json.dumps(_gappy_design()))
        self.job.artifacts.create(kind="mission", schema_version="mission/v1", content_hash="m", body=json.dumps({"target_height_m": 0.65}))

    @patch("core.image_design.propose_single_revision")
    def test_propose_returns_stored_proposal(self, propose):
        propose.return_value = {
            "schema_version": "image-robot-revision/v1",
            "rationale": "Close the mast gap.",
            "edits": [{"component_id": "mast", "position_m": [0, 0, 0.15], "reason": "Seat the mast on the deck."}],
            "requires_human_review": True,
        }
        response = self.client.post(f"/api/v1/jobs/{self.job.id}/propose-design-revision", data=json.dumps({}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["requires_human_review"])
        self.assertTrue(self.job.artifacts.filter(kind="revision_proposal").exists())
        propose.assert_called_once()

    @patch("core.image_design_worker.start_image_design")
    def test_image_design_accepts_labeled_extra_views(self, start):
        from django.core.files.uploadedfile import SimpleUploadedFile

        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        response = self.client.post(
            f"/api/v1/projects/{self.project.id}/image-design",
            data={
                "brief": "multi-view rover",
                "target_height_m": "0.5",
                "constraints": "",
                "idempotency_key": "multi-1",
                "view": "front",
                "view_2": "side",
                "image": SimpleUploadedFile("front.png", png, content_type="image/png"),
                "image_2": SimpleUploadedFile("side.png", png, content_type="image/png"),
            },
        )
        self.assertEqual(response.status_code, 202, response.content)
        *_, extra, _primary = start.call_args.args
        self.assertEqual(len(extra), 1)
        self.assertEqual(extra[0][2], "side")

    @patch("core.image_design_worker.start_image_design")
    def test_image_design_local_only_flag_reaches_worker(self, start):
        from django.core.files.uploadedfile import SimpleUploadedFile

        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        response = self.client.post(
            f"/api/v1/projects/{self.project.id}/image-design",
            data={
                "brief": "local rover",
                "target_height_m": "0.5",
                "constraints": "",
                "idempotency_key": "local-1",
                "view": "front",
                "local_only": "1",
                "image": SimpleUploadedFile("front.png", png, content_type="image/png"),
            },
        )
        self.assertEqual(response.status_code, 202, response.content)
        self.assertTrue(start.call_args.kwargs.get("local_only"))

    def test_propose_with_clean_design_reports_nothing_to_do(self):
        clean = _gappy_design()
        clean["components"][1]["position_m"] = [0, 0, 0.15]
        clean["components"][2]["position_m"] = [0, 0, 0.27]
        spec = self.job.artifacts.get(kind="spec")
        spec.body = json.dumps(clean)
        spec.save(update_fields=["body"])
        response = self.client.post(f"/api/v1/jobs/{self.job.id}/propose-design-revision", data=json.dumps({}), content_type="application/json")
        self.assertEqual(response.status_code, 409)

    @patch("core.freecad_export.run_freecad_export")
    def test_apply_rebuilds_and_rejects_unreviewed(self, export):
        export.return_value = {"status": "completed", "freecad_version": "test"}
        forged = {"schema_version": "image-robot-revision/v1", "rationale": "x", "edits": [{"component_id": "mast", "position_m": [0, 0, 0.15]}]}
        response = self.client.post(
            f"/api/v1/jobs/{self.job.id}/apply-design-revision",
            data=json.dumps({"proposal": forged}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        export.assert_not_called()

        approved = {**forged, "requires_human_review": True}
        response = self.client.post(
            f"/api/v1/jobs/{self.job.id}/apply-design-revision",
            data=json.dumps({"proposal": approved}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        specs = self.job.artifacts.filter(kind="spec").order_by("id")
        self.assertEqual(specs.count(), 2)
        latest = json.loads(specs.last().body)
        mast = next(item for item in latest["components"] if item["id"] == "mast")
        self.assertAlmostEqual(mast["position_m"][2], 0.15)
