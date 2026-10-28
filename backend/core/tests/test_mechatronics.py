from django.test import SimpleTestCase

from core.image_design import simulate_image_robot
from core.mechatronics import (
    compute_power_and_battery_sizing,
    generate_deployable_firmware_package,
    generate_pcb_and_wiring_architecture,
)


def _sample_rover_design():
    return {
        "schema_version": "image-robot-concept/v1",
        "title": "Compact Two-Tier Rover",
        "robot_type": "differential_drive_rover",
        "components": [
            {"id": "lower_chassis", "name": "Lower Chassis Plate", "role": "base plate", "geometry": "box", "dimensions_m": [0.16, 0.22, 0.005], "position_m": [0.0, 0.0, 0.035], "parent_id": None, "color": "#2b2d30"},
            {"id": "upper_chassis", "name": "Upper Electronics Deck", "role": "upper deck", "geometry": "box", "dimensions_m": [0.15, 0.20, 0.005], "position_m": [0.0, 0.0, 0.085], "parent_id": "lower_chassis", "color": "#2b2d30"},
            {"id": "wheel_left", "name": "Left Drive Wheel", "role": "drive wheel", "geometry": "cylinder", "dimensions_m": [0.068, 0.068, 0.026], "position_m": [-0.095, 0.02, 0.034], "parent_id": "lower_chassis", "color": "#eab308"},
            {"id": "wheel_right", "name": "Right Drive Wheel", "role": "drive wheel", "geometry": "cylinder", "dimensions_m": [0.068, 0.068, 0.026], "position_m": [0.095, 0.02, 0.034], "parent_id": "lower_chassis", "color": "#eab308"},
            {"id": "rear_caster", "name": "Rear Ball Caster", "role": "support caster", "geometry": "sphere", "dimensions_m": [0.024, 0.024, 0.024], "position_m": [0.0, -0.085, 0.012], "parent_id": "lower_chassis", "color": "#94a3b8"},
            {"id": "battery_pack", "name": "3S LiPo Battery Pack", "role": "power storage", "geometry": "box", "dimensions_m": [0.075, 0.11, 0.026], "position_m": [0.0, -0.03, 0.051], "parent_id": "lower_chassis", "color": "#2563eb"},
            {"id": "motor_driver", "name": "Dual H-Bridge Motor Driver", "role": "motor driver", "geometry": "box", "dimensions_m": [0.045, 0.045, 0.022], "position_m": [0.0, 0.05, 0.049], "parent_id": "lower_chassis", "color": "#dc2626"},
            {"id": "controller_board", "name": "Main Controller Motherboard", "role": "compute board", "geometry": "box", "dimensions_m": [0.085, 0.056, 0.018], "position_m": [0.0, -0.01, 0.097], "parent_id": "upper_chassis", "color": "#16a34a"},
            {"id": "sonar_left", "name": "Front Left Ultrasonic Sensor", "role": "obstacle rangefinder", "geometry": "box", "dimensions_m": [0.045, 0.020, 0.016], "position_m": [-0.04, 0.09, 0.096], "parent_id": "upper_chassis", "color": "#0284c7"},
            {"id": "sonar_right", "name": "Front Right Ultrasonic Sensor", "role": "obstacle rangefinder", "geometry": "box", "dimensions_m": [0.045, 0.020, 0.016], "position_m": [0.04, 0.09, 0.096], "parent_id": "upper_chassis", "color": "#0284c7"},
        ],
    }


class MechatronicsAndPcbTests(SimpleTestCase):
    def setUp(self):
        self.design = _sample_rover_design()

    def test_power_and_battery_sizing_scales_with_tire_weight_and_runtime(self):
        base_3h = compute_power_and_battery_sizing(
            design=self.design,
            total_mass_kg=1.50,
            wheel_diameter_m=0.068,
            wheel_count=2,
            track_width_m=0.18,
            cruise_speed_m_s=0.35,
            target_runtime_hours=3.0,
            extra_payload_kg=0.0,
        )
        heavy_3h = compute_power_and_battery_sizing(
            design=self.design,
            total_mass_kg=1.50,
            wheel_diameter_m=0.068,
            wheel_count=2,
            track_width_m=0.18,
            cruise_speed_m_s=0.35,
            target_runtime_hours=3.0,
            extra_payload_kg=1.5,
        )
        self.assertGreater(
            heavy_3h["mass_budget"]["normal_load_per_tire_n"],
            base_3h["mass_budget"]["normal_load_per_tire_n"],
        )
        self.assertGreater(
            heavy_3h["drivetrain_energetics"]["continuous_torque_per_wheel_nm"],
            base_3h["drivetrain_energetics"]["continuous_torque_per_wheel_nm"],
        )
        self.assertGreater(
            heavy_3h["battery_recommendation"]["required_capacity_mah"],
            base_3h["battery_recommendation"]["required_capacity_mah"],
        )
        self.assertEqual(len(base_3h["tire_weight_sensitivity_table"]), 4)

    def test_pcb_and_wiring_generator_produces_valid_kicad_and_erc(self):
        power = compute_power_and_battery_sizing(
            design=self.design,
            total_mass_kg=1.50,
            wheel_diameter_m=0.068,
            wheel_count=2,
            track_width_m=0.18,
            cruise_speed_m_s=0.35,
            target_runtime_hours=3.0,
        )
        pcb_wiring = generate_pcb_and_wiring_architecture(self.design, power)
        board = pcb_wiring["pcb_board"]
        self.assertEqual(len(board["mounting_holes_mm"]), 4)
        self.assertGreaterEqual(len(board["footprints"]), 6)
        self.assertGreaterEqual(len(board["tracks"]), 10)
        self.assertTrue(all(chk["status"] == "passed" for chk in pcb_wiring["erc_checks"]))

        kicad_pcb = pcb_wiring["kicad_files"]["forgebot_carrier.kicad_pcb"]
        kicad_sch = pcb_wiring["kicad_files"]["forgebot_carrier.kicad_sch"]
        self.assertIn("(kicad_pcb", kicad_pcb)
        self.assertIn("MountingHole_3.2mm_M3", kicad_pcb)
        self.assertIn("(segment", kicad_pcb)
        self.assertIn("(kicad_sch", kicad_sch)
        self.assertGreaterEqual(len(pcb_wiring.get("wiring_harness_3d", [])), 6)
        self.assertTrue(all(len(cable["points_m"]) >= 3 for cable in pcb_wiring["wiring_harness_3d"]))

    def test_deployable_firmware_compiles_and_runs_vision_sonar_fusion(self):
        sim = simulate_image_robot(self.design)
        self.assertIn("mechatronics", sim)
        self.assertIn("wiring_harness_3d", sim["robot_preview"])
        self.assertGreaterEqual(len(sim["robot_preview"]["wiring_harness_3d"]), 6)

        obstacle_training = sim.get("obstacle_training", {})
        self.assertTrue(obstacle_training.get("mujoco_enabled"))
        self.assertIn("<mujoco", obstacle_training.get("mjcf_xml", ""))

        mech = sim["mechatronics"]
        files = mech["firmware_files"]
        for expected in (
            "run_robot.py",
            "hardware_pinout.py",
            "vision_obstacle_detector.py",
            "obstacle_avoidance_controller.py",
            "pcb/forgebot_carrier.kicad_pcb",
            "sim/forgebot_rover_mjcf.xml",
        ):
            self.assertIn(expected, files)

        # Execute the generated vision detector and controller code in an isolated namespace
        ns = {}
        exec(compile(files["vision_obstacle_detector.py"], "vision_obstacle_detector.py", "exec"), ns)
        exec(compile(files["obstacle_avoidance_controller.py"], "obstacle_avoidance_controller.py", "exec"), ns)

        detector = ns["VisionAndSonarObstacleDetector"]()
        percept = detector.fuse_percepts(0.85, 0.90, (0.35, 0.94, 0.2, 0.3))
        self.assertTrue(percept.vision_detected)
        self.assertEqual(percept.source, "vision_camera")

        cmd = ns["compute_cmd_vel"](0.0, percept.min_distance_m, percept.bearing_rad)
        self.assertIn("pwm_left_duty", cmd)
        self.assertIn("pwm_right_duty", cmd)
        # Since obstacle is ahead-left (+bearing), repulsive steering turns right (negative omega)
        self.assertLess(cmd["angular_omega_rad_s"], 0.0)

