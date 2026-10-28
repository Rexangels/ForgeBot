import unittest
from core.fabrication import (
    PRINT_MATERIALS,
    estimate_3d_print_and_fabrication_costs,
)


class TestFabricationEstimator(unittest.TestCase):
    def test_fabrication_estimator_basic(self):
        sample_design = {
            "title": "Test Rover",
            "components": [
                {
                    "id": "chassis",
                    "name": "Base Chassis",
                    "role": "structural",
                    "geometry": "box",
                    "dimensions_m": [0.20, 0.15, 0.02],
                },
                {
                    "id": "wheel_l",
                    "name": "Drive Wheel Left",
                    "role": "locomotion",
                    "geometry": "cylinder",
                    "dimensions_m": [0.06, 0.06, 0.025],
                },
            ],
        }

        sample_cad_export = {
            "export_report": {
                "solid_kernel_validation": {
                    "component_validity": [
                        {"component_id": "chassis", "valid": True, "volume_mm3": 600000.0},
                        {"component_id": "wheel_l", "valid": True, "volume_mm3": 70685.8},
                    ]
                }
            }
        }

        result = estimate_3d_print_and_fabrication_costs(
            sample_design,
            cad_export=sample_cad_export,
            material_key="pla",
            infill_percent=25.0,
            bed_width_mm=220.0,
        )

        self.assertEqual(result["schema_version"], "forgebot-fabrication-estimator/v1")
        self.assertEqual(result["material"]["id"], "pla")
        summary = result["summary"]
        self.assertEqual(summary["total_printed_parts"], 2)
        self.assertGreater(summary["total_mass_grams"], 50.0)
        self.assertGreater(summary["total_print_time_hours"], 1.0)
        self.assertGreater(summary["grand_total_build_cost_usd"], 80.0)
        self.assertIn("cots_bom", result)
        self.assertTrue(len(result["cots_bom"]) >= 4)

    def test_materials_catalog(self):
        self.assertIn("pla", PRINT_MATERIALS)
        self.assertIn("pa_cf", PRINT_MATERIALS)
        self.assertGreater(PRINT_MATERIALS["pa_cf"]["cost_per_kg_usd"], 30.0)


if __name__ == "__main__":
    unittest.main()
