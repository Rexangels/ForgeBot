"""Arm-path hardening: retry policy, schema fixtures, motor sizing."""

import copy
import json
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from django.test import SimpleTestCase

from core.meta_model import MetaModelError, _request_json
from core.robot_arm import ARM_DESIGN_SCHEMA, validate_arm_design

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "arm_spec_valid.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class RetryPolicyTests(SimpleTestCase):
    def _http_error(self, code):
        return HTTPError("http://x", code, "boom", {}, None)

    def _ok_response(self, payload=b'{"ok": true}'):
        from unittest.mock import MagicMock

        response = MagicMock()
        response.read.return_value = payload
        response.__enter__.return_value = response
        return response

    @patch("time.sleep")
    @patch("core.meta_model.urlopen")
    def test_rate_limit_then_success(self, urlopen, sleep):
        urlopen.side_effect = [self._http_error(429), self._ok_response()]
        self.assertEqual(_request_json("/x", {"a": 1}), {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1.0)

    @patch("time.sleep")
    @patch("core.meta_model.urlopen")
    def test_server_errors_exhaust_attempts(self, urlopen, sleep):
        urlopen.side_effect = self._http_error(503)
        with self.assertRaises(MetaModelError):
            _request_json("/x", None)
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("time.sleep")
    @patch("core.meta_model.urlopen")
    def test_auth_failure_does_not_retry(self, urlopen, sleep):
        urlopen.side_effect = self._http_error(401)
        with self.assertRaises(MetaModelError):
            _request_json("/x", None)
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()


class ArmSchemaFixtureTests(SimpleTestCase):
    def test_fixture_matches_schema_contract(self):
        spec = load_fixture()
        self.assertEqual(set(spec), set(ARM_DESIGN_SCHEMA["required"]))
        self.assertEqual(validate_arm_design(spec), [])

    def test_each_required_key_is_guarded(self):
        for key in ARM_DESIGN_SCHEMA["required"]:
            with self.subTest(key=key):
                spec = load_fixture()
                del spec[key]
                self.assertTrue(validate_arm_design(spec), key)

    def test_undersized_actuators_are_rejected(self):
        spec = load_fixture()
        spec["joint_limits"][1]["effort_nm"] = 0.2
        spec["joint_limits"][2]["effort_nm"] = 0.2
        spec["gripper"]["grip_force_n"] = 0.5
        errors = validate_arm_design(spec)
        self.assertTrue(any("shoulder_pitch" in item for item in errors))
        self.assertTrue(any("elbow_pitch" in item for item in errors))
        self.assertTrue(any("grip force" in item for item in errors))

    def test_fixture_sizing_has_margin(self):
        spec = load_fixture()
        for index in (1, 2):
            spec["joint_limits"][index]["effort_nm"] = 3.0
        self.assertEqual(validate_arm_design(spec), [])

    def test_broken_shapes_fail_fast_without_sizing_noise(self):
        self.assertIn("design must be an object", validate_arm_design(None))
        spec = load_fixture()
        spec["payload_kg"] = 9.0
        self.assertTrue(validate_arm_design(spec))
