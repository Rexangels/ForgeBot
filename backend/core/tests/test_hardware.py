"""Tests for standard mechanical hardware registry, fasteners, and hole patterns."""

from django.test import SimpleTestCase

from core.hardware import (
    ACTUATOR_MOUNT_PATTERNS,
    COTS_FOOTPRINTS,
    METRIC_FASTENERS,
    calculate_four_hole_pattern,
    check_fastener_alignment,
    generate_scad_hole_subtraction,
    get_fastener,
    get_mount_pattern,
    match_cots_footprint,
    project_assembly_mating_holes,
)


class HardwareRegistryTests(SimpleTestCase):
    def test_get_fasteners(self):
        m3 = get_fastener("m3")
        self.assertEqual(m3["nominal_dia_mm"], 3.0)
        self.assertGreater(m3["clearance_standard_mm"], 3.0)
        self.assertGreater(m3["head_dia_mm"], m3["clearance_standard_mm"])
        with self.assertRaises(ValueError):
            get_fastener("M99")

    def test_actuator_patterns(self):
        nema17 = get_mount_pattern("nema17")
        self.assertEqual(nema17["hole_pitch_mm"], [31.0, 31.0])
        self.assertEqual(nema17["fastener"], "M3")
        self.assertEqual(nema17["pilot_dia_mm"], 22.0)

        servo = get_mount_pattern("standard_servo")
        self.assertEqual(servo["fastener"], "M3")
        self.assertEqual(len(servo["hole_pitch_mm"]), 2)

    def test_four_hole_pattern_calculation(self):
        pattern = calculate_four_hole_pattern(width_mm=50.0, height_mm=40.0, fastener="M3")
        self.assertEqual(pattern["status"], "ready")
        self.assertEqual(len(pattern["holes"]), 4)
        self.assertGreater(pattern["pitch_x_mm"], 20.0)
        self.assertGreater(pattern["pitch_y_mm"], 15.0)

    def test_tiny_face_declines_pattern(self):
        pattern = calculate_four_hole_pattern(width_mm=3.0, height_mm=3.0, fastener="M3")
        self.assertEqual(pattern["status"], "too_small")
        self.assertEqual(pattern["holes"], [])

    def test_scad_hole_subtraction(self):
        pattern = calculate_four_hole_pattern(width_mm=40.0, height_mm=40.0, fastener="M3")
        scad = generate_scad_hole_subtraction(pattern, through_depth_mm=10.0, add_counterbore=True)
        self.assertIn("cylinder", scad)
        self.assertIn("translate", scad)
        self.assertIn("Fastener pattern: M3", scad)

    def test_fastener_alignment_check(self):
        p1 = calculate_four_hole_pattern(width_mm=40.0, height_mm=40.0, fastener="M3")
        p2 = calculate_four_hole_pattern(width_mm=40.0, height_mm=40.0, fastener="M3")
        report = check_fastener_alignment(p1, p2)
        self.assertTrue(report["aligned"])
        self.assertEqual(report["status"], "aligned")

        # Misaligned pitch
        p3 = calculate_four_hole_pattern(width_mm=60.0, height_mm=40.0, fastener="M3")
        report_mismatch = check_fastener_alignment(p1, p3)
        self.assertFalse(report_mismatch["aligned"])
        self.assertEqual(report_mismatch["status"], "misaligned")

    def test_cots_footprint_matching(self):
        sonar = {"id": "ultrasonic_mount", "name": "HC-SR04 Ultrasonic Sensor", "role": "obstacle rangefinder", "dimensions_m": [0.045, 0.020, 0.015]}
        cots = match_cots_footprint(sonar)
        self.assertIsNotNone(cots)
        self.assertEqual(cots["cots_id"], "hc_sr04")
        self.assertEqual(cots["fastener"], "M2")
        self.assertEqual(len(cots["mounting_holes_local_mm"]), 4)

        standoff = {"id": "standoff_fl", "name": "Front Left Standoff", "role": "deck spacer", "dimensions_m": [0.006, 0.006, 0.035]}
        scots = match_cots_footprint(standoff)
        self.assertIsNotNone(scots)
        self.assertEqual(scots["bore_diameter_mm"], 3.2)

    def test_cross_part_mating_hole_projection(self):
        components = [
            {"id": "lower_plate", "name": "Lower Chassis Plate", "role": "base deck", "parent_id": None, "geometry": "box", "dimensions_m": [0.18, 0.24, 0.005], "position_m": [0.0, 0.0, 0.05]},
            {"id": "upper_plate", "name": "Upper Chassis Plate", "role": "top deck", "parent_id": "lower_plate", "geometry": "box", "dimensions_m": [0.18, 0.24, 0.005], "position_m": [0.0, 0.0, 0.09]},
            {"id": "standoff_fl", "name": "Standoff FL", "role": "spacer pillar", "parent_id": "lower_plate", "geometry": "cylinder", "dimensions_m": [0.008, 0.008, 0.035], "position_m": [-0.07, 0.09, 0.07]},
            {"id": "standoff_fr", "name": "Standoff FR", "role": "spacer pillar", "parent_id": "lower_plate", "geometry": "cylinder", "dimensions_m": [0.008, 0.008, 0.035], "position_m": [0.07, 0.09, 0.07]},
            {"id": "control_board", "name": "Arduino Control Board", "role": "microcontroller pcb", "parent_id": "upper_plate", "geometry": "box", "dimensions_m": [0.068, 0.053, 0.012], "position_m": [0.0, 0.0, 0.0985]},
        ]
        holes = project_assembly_mating_holes(components)
        lower_sources = {h["source"] for h in holes["lower_plate"]}
        upper_sources = {h["source"] for h in holes["upper_plate"]}
        self.assertIn("mate:standoff_fl", lower_sources)
        self.assertIn("mate:standoff_fr", lower_sources)
        self.assertIn("mate:standoff_fl", upper_sources)
        self.assertIn("mate:standoff_fr", upper_sources)
        self.assertIn("mate:control_board", upper_sources)

