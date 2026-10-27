"""Focused tests: job state machine, URDF validation, rover acceptance."""

from django.test import SimpleTestCase

from core import schemas, states
from core.rover import (
    generate_rover_bom,
    generate_rover_spec,
    generate_rover_urdf,
    generate_starter_code,
)
from core.simulation import run_waypoint_route
from core.validation import (
    SEVERITY_BLOCKER,
    check_generated_code,
    validate_urdf,
)


class JobStateTests(SimpleTestCase):
    def test_happy_path_to_review(self):
        self.assertTrue(states.can_transition(states.QUEUED, states.SPECIFYING))
        self.assertTrue(
            states.can_transition(states.SPECIFYING, states.AWAITING_REVIEW)
        )
        self.assertTrue(
            states.can_transition(states.AWAITING_REVIEW, states.GENERATING)
        )

    def test_validation_can_complete_package_or_continue_to_simulation(self):
        # Some workflows produce a reviewable package without a simulator;
        # workflows with a simulation gate transition through SIMULATING.
        self.assertTrue(states.can_transition(states.VALIDATING, states.SUCCEEDED))
        self.assertTrue(states.can_transition(states.VALIDATING, states.SIMULATING))
        self.assertTrue(states.can_transition(states.VALIDATING, states.FAILED))

    def test_illegal_transition_raises(self):
        with self.assertRaises(ValueError):
            states.check_transition(states.QUEUED, states.SUCCEEDED)

    def test_terminal_states_stick(self):
        for terminal in (
            states.SUCCEEDED,
            states.FAILED,
            states.CANCELLED,
        ):
            self.assertFalse(states.can_transition(terminal, states.SIMULATING))


class RoverArtifactTests(SimpleTestCase):
    def test_spec_and_bom_validate(self):
        self.assertEqual(schemas.validate_spec(generate_rover_spec()), [])
        self.assertEqual(schemas.validate_bom(generate_rover_bom()), [])

    def test_generated_urdf_has_no_blockers(self):
        findings = validate_urdf(generate_rover_urdf())
        blockers = [f for f in findings if f.severity == SEVERITY_BLOCKER]
        self.assertEqual(blockers, [])

    def test_broken_urdf_tree_is_blocked(self):
        bad = """<robot name="x">
          <link name="a"/><link name="a"/>
          <joint name="j" type="continuous">
            <parent link="a"/><child link="missing"/>
          </joint>
        </robot>"""
        findings = validate_urdf(bad)
        self.assertTrue(
            any(f.severity == SEVERITY_BLOCKER for f in findings)
        )

    def test_forbidden_code_pattern_is_blocked(self):
        findings = check_generated_code("import os\nos.system('rm -rf /')")
        self.assertTrue(
            any(f.severity == SEVERITY_BLOCKER for f in findings)
        )

    def test_starter_code_is_clean(self):
        self.assertEqual(
            [
                f
                for f in check_generated_code(generate_starter_code())
                if f.severity == SEVERITY_BLOCKER
            ],
            [],
        )


class RoverAcceptanceTests(SimpleTestCase):
    route = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]

    def test_clean_route_passes(self):
        result = run_waypoint_route(self.route, speed_m_s=0.8)
        self.assertTrue(result.passed)
        self.assertEqual(result.collisions, 0)

    def test_collision_fails(self):
        result = run_waypoint_route(
            self.route, speed_m_s=0.8, obstacles=[((1.0, 0.0), 0.3)]
        )
        self.assertFalse(result.passed)
        self.assertGreater(result.collisions, 0)

    def test_speed_limit_violation_fails(self):
        result = run_waypoint_route(self.route, speed_m_s=5.0)
        self.assertFalse(result.passed)
