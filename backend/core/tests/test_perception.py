"""Perception hardening: calibration math, accuracy, tracking, endpoints."""

import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from core.models import Project
from core.perception import (
    WORK_AREA_CORNERS,
    apply_homography,
    estimate_detection_3d,
    leave_one_out_error,
    map_detection,
    solve_homography,
    track_objects,
    triangulate_multi_view,
)

# Exact affine projection of WORK_AREA_CORNERS, so hold-out error is ~zero.
IMAGE = [[0.05, 0.045], [0.85, 0.085], [0.85, 0.805], [0.05, 0.765]]


class HomographyTests(SimpleTestCase):
    def test_solve_then_apply_round_trips_corners(self):
        homography = solve_homography(IMAGE, WORK_AREA_CORNERS)
        for image, world in zip(IMAGE, WORK_AREA_CORNERS):
            mapped = apply_homography(image, homography)
            self.assertAlmostEqual(mapped[0], world[0], places=6)
            self.assertAlmostEqual(mapped[1], world[1], places=6)

    def test_exact_corners_measure_zero_error(self):
        accuracy = leave_one_out_error(IMAGE, WORK_AREA_CORNERS)
        self.assertLess(accuracy["max_m"], 0.005)
        self.assertEqual(len(accuracy["per_corner_m"]), 4)

    def test_sloppy_click_shows_in_error(self):
        sloppy = [list(point) for point in IMAGE]
        sloppy[2][0] += 0.2
        accuracy = leave_one_out_error(sloppy, WORK_AREA_CORNERS)
        exact = leave_one_out_error(IMAGE, WORK_AREA_CORNERS)
        self.assertGreater(accuracy["max_m"], exact["max_m"] + 0.01)

    def test_degenerate_corners_rejected(self):
        with self.assertRaises(ValueError):
            solve_homography([[0, 0]] * 4, WORK_AREA_CORNERS)

    def test_detection_mapping_records_depth_source(self):
        homography = solve_homography(IMAGE, WORK_AREA_CORNERS)
        region = {"x_min": 0.4, "y_min": 0.4, "x_max": 0.6, "y_max": 0.6}
        assumed = map_detection(region, homography)
        self.assertEqual(assumed["depth_source"], "assumed_plane_40mm")
        measured = map_detection(region, homography, depth_m=0.07)
        self.assertEqual((measured["z"], measured["depth_source"]), (0.07, "measured"))
        with self.assertRaises(ValueError):
            map_detection({**region, "x_max": 0.1}, homography)

    def test_estimate_detection_3d_envelope(self):
        homography = solve_homography(IMAGE, WORK_AREA_CORNERS)
        region = {"x_min": 0.4, "y_min": 0.4, "x_max": 0.6, "y_max": 0.6}
        est = estimate_detection_3d(region, homography)
        self.assertIn("bounding_box_3d", est)
        self.assertIn("grasp_target", est)
        self.assertGreater(est["height_m"], 0.01)
        self.assertGreater(est["width_m"], 0.01)
        self.assertEqual(est["bounding_box_3d"]["center"][0], est["x"])
        self.assertEqual(est["bounding_box_3d"]["center"][1], est["y"])
        self.assertAlmostEqual(est["bounding_box_3d"]["center"][2], est["height_m"] / 2.0, places=4)
        self.assertGreater(est["grasp_target"]["approach_z"], est["grasp_target"]["z"])

    def test_triangulate_multi_view(self):
        h_a = solve_homography(IMAGE, WORK_AREA_CORNERS)
        h_b = solve_homography(IMAGE, WORK_AREA_CORNERS)
        # Point on ground
        target_ground = [0.3, 0.0]
        # Camera A at [0, -0.6, 0.8], Camera B at [0.6, 0.0, 0.8]
        cam_a = [0.0, -0.6, 0.8]
        cam_b = [0.6, 0.0, 0.8]
        # Image coordinates corresponding to target_ground (using inverse or direct test mapping)
        # If both cameras observe target_ground at their corresponding image points:
        pt_a = [0.45, 0.425]  # approx center in image
        pt_b = [0.45, 0.425]
        res = triangulate_multi_view(pt_a, h_a, cam_a, pt_b, h_b, cam_b)
        self.assertIn("x", res)
        self.assertIn("y", res)
        self.assertIn("z", res)
        self.assertEqual(res["depth_source"], "multi_view_triangulation")


class TrackingTests(SimpleTestCase):
    def _box(self, label, box, track_id=None, frames=1):
        item = {"label": label, "image_region": dict(zip(("x_min", "y_min", "x_max", "y_max"), box)), "frames_seen": frames}
        if track_id is not None:
            item["track_id"] = track_id
        return item

    def test_stable_object_keeps_track_id(self):
        previous = [self._box("cube", (0.1, 0.1, 0.3, 0.3), track_id=1, frames=2)]
        current = [self._box("cube", (0.12, 0.11, 0.31, 0.3))]
        tracks = track_objects(previous, current)
        self.assertEqual([(item["track_id"], item["frames_seen"]) for item in tracks], [(1, 3)])

    def test_new_and_moved_objects_open_tracks(self):
        previous = [self._box("cube", (0.1, 0.1, 0.3, 0.3), track_id=1)]
        current = [self._box("cube", (0.6, 0.6, 0.8, 0.8)), self._box("ball", (0.1, 0.1, 0.3, 0.3))]
        tracks = track_objects(previous, current)
        self.assertEqual([item["track_id"] for item in tracks], [2, 3])

    def test_label_change_breaks_track(self):
        previous = [self._box("cube", (0.1, 0.1, 0.3, 0.3), track_id=1)]
        current = [self._box("box", (0.1, 0.1, 0.3, 0.3))]
        self.assertEqual(track_objects(previous, current)[0]["track_id"], 2)


class CalibrationEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("eng", password="pw")
        self.client.force_login(self.user)
        self.project = Project.objects.create(name="vis", owner=self.user)

    def test_save_and_reload_calibration(self):
        response = self.client.post(
            f"/api/v1/projects/{self.project.id}/calibrations",
            data=json.dumps({"label": "bench", "image_points": IMAGE}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertIn("accuracy_m", body)
        latest = self.client.get(f"/api/v1/projects/{self.project.id}/calibrations/latest")
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.json()["id"], body["id"])
        self.assertEqual(len(latest.json()["homography"]), 8)

    def test_bad_corners_rejected(self):
        response = self.client.post(
            f"/api/v1/projects/{self.project.id}/calibrations",
            data=json.dumps({"image_points": [[0, 0]] * 4}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_track_endpoint_matches_frames(self):
        previous = [{"label": "cube", "image_region": {"x_min": 0.1, "y_min": 0.1, "x_max": 0.3, "y_max": 0.3}, "track_id": 4, "frames_seen": 2}]
        current = [{"label": "cube", "image_region": {"x_min": 0.11, "y_min": 0.1, "x_max": 0.31, "y_max": 0.3}}]
        response = self.client.post(
            "/api/v1/vision/track",
            data=json.dumps({"previous": previous, "current": current}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        track = response.json()["tracks"][0]
        self.assertEqual((track["track_id"], track["frames_seen"]), (4, 3))
