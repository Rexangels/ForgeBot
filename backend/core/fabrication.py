"""3D Printability, Slicing Physics, and Fabrication Cost Estimator for ForgeBot.

Calculates real-world 3D printing filament mass (g), print duration (hrs),
build-bed fit verification (against standard 220x220x250 mm volumes), and
Commercial Off-The-Shelf (COTS) electronics/fastener BOM costs based on
exact OpenCASCADE CAD solid volumes and component dimensions.
"""

from __future__ import annotations

import math
from typing import Any

# Standard physical materials and densities (g/cm^3) & market pricing ($/kg)
PRINT_MATERIALS: dict[str, dict[str, Any]] = {
    "pla": {
        "id": "pla",
        "name": "PLA (Standard / Tough)",
        "density_g_cm3": 1.24,
        "cost_per_kg_usd": 20.00,
        "nozzle_temp_c": 210,
        "bed_temp_c": 60,
        "description": "Rigid, easy to print, excellent dimensional accuracy for structural robotics brackets.",
    },
    "petg": {
        "id": "petg",
        "name": "PETG (Impact & Chemical Resistant)",
        "density_g_cm3": 1.27,
        "cost_per_kg_usd": 22.00,
        "nozzle_temp_c": 240,
        "bed_temp_c": 80,
        "description": "High toughness, UV resistant, semi-flexible impact absorption for outdoor rovers.",
    },
    "abs": {
        "id": "abs",
        "name": "ABS / ASA (UV & Thermal Resistant)",
        "density_g_cm3": 1.04,
        "cost_per_kg_usd": 24.00,
        "nozzle_temp_c": 255,
        "bed_temp_c": 100,
        "description": "Lightweight, heat resistant to 95°C, acetone-vapor smoothable for motor enclosures.",
    },
    "pa_cf": {
        "id": "pa_cf",
        "name": "PA-CF (Carbon-Fiber Reinforced Nylon)",
        "density_g_cm3": 1.15,
        "cost_per_kg_usd": 48.00,
        "nozzle_temp_c": 280,
        "bed_temp_c": 90,
        "description": "High stiffness-to-weight ratio, structural load-bearing avian chassis and walking legs.",
    },
    "tpu": {
        "id": "tpu",
        "name": "TPU 95A (Flexible Polyurethane)",
        "density_g_cm3": 1.21,
        "cost_per_kg_usd": 28.00,
        "nozzle_temp_c": 225,
        "bed_temp_c": 50,
        "description": "High-grip shock absorption for robot tires, claw pads, and bumper cushions.",
    },
}

# Standard kinematics for modern high-speed CoreXY & bed-slinger 3D printers
HOTEND_VOLUMETRIC_FLOW_MM3_S = 14.0  # Max volumetric flow for standard 0.4mm nozzle
LAYER_HEIGHT_MM = 0.20
SHELL_WALLS = 3
SHELL_THICKNESS_MM = 1.20  # 3 x 0.4mm line width
TOP_BOTTOM_LAYERS = 4
TOP_BOTTOM_THICKNESS_MM = 0.80  # 4 x 0.20mm
PRINTER_POWER_KW = 0.12  # Average 120W heated bed + hotend
ELECTRICITY_COST_KWH_USD = 0.15  # Average US/EU residential energy rate

# Standard COTS hardware pricing catalog
COTS_CATALOG: list[dict[str, Any]] = [
    {
        "keywords": ("servo", "knee", "hip", "ankle", "wing_act", "claw"),
        "name": "TowerPro MG996R Metal-Gear High-Torque Servo (11 kg·cm)",
        "category": "Actuators & Servos",
        "unit_price_usd": 7.20,
        "supplier": "TowerPro / Amazon / LCSC",
        "part_number": "MG996R-180",
    },
    {
        "keywords": ("motor", "wheel", "drive", "gearmotor"),
        "name": "N20 Micro Metal Gearmotor w/ Quadrature Hall Encoder (6V 300RPM)",
        "category": "Actuators & Servos",
        "unit_price_usd": 6.50,
        "supplier": "Pololu / DFRobot",
        "part_number": "POLOLU-3072",
    },
    {
        "keywords": ("driver", "tb6612", "l298", "esc", "h-bridge"),
        "name": "TB6612FNG Dual H-Bridge 1.2A Motor Driver Module",
        "category": "Motor Drivers",
        "unit_price_usd": 4.80,
        "supplier": "Adafruit / SparkFun",
        "part_number": "ADA-2448",
    },
    {
        "keywords": ("raspberry", "pi", "controller", "motherboard", "core", "chest", "torso"),
        "name": "Raspberry Pi 4 Model B (4GB RAM) Quad-Core Robotics SBC",
        "category": "Compute & Processing",
        "unit_price_usd": 45.00,
        "supplier": "Raspberry Pi Foundation / DigiKey",
        "part_number": "RPI4-MODBP-4GB",
    },
    {
        "keywords": ("esp32", "microcontroller", "mcu", "nano", "pico"),
        "name": "ESP32-S3 Dual-Core Xtensa LX7 Microcontroller Module (Wi-Fi/BLE)",
        "category": "Compute & Processing",
        "unit_price_usd": 4.50,
        "supplier": "Espressif / LCSC",
        "part_number": "ESP32-S3-WROOM-1",
    },
    {
        "keywords": ("camera", "optical", "eye", "lens", "vision"),
        "name": "Sony IMX219 8MP 160° Wide-Angle Robotics Camera Module (CSI-2)",
        "category": "Perception Sensors",
        "unit_price_usd": 14.50,
        "supplier": "Arducam / Waveshare",
        "part_number": "B0393-IMX219",
    },
    {
        "keywords": ("sonar", "ultrasonic", "rangefinder", "antenna"),
        "name": "HC-SR04 Ultrasonic Distance Sensor Module (2–400 cm)",
        "category": "Perception Sensors",
        "unit_price_usd": 1.95,
        "supplier": "SparkFun / LCSC",
        "part_number": "SEN-15569",
    },
    {
        "keywords": ("battery", "lipo", "power", "cell"),
        "name": "3S 11.1V 2200mAh 35C LiPo Battery Pack (XT60)",
        "category": "Power System",
        "unit_price_usd": 18.50,
        "supplier": "Gens Ace / Amazon",
        "part_number": "GA-B-35C-2200-3S1P",
    },
    {
        "keywords": ("regulator", "buck", "bec", "stepdown"),
        "name": "High-Efficiency 5V/5A DC-DC Step-Down Buck Converter (UBEC)",
        "category": "Power System",
        "unit_price_usd": 5.20,
        "supplier": "Hobbywing / Amazon",
        "part_number": "HW-UBEC-5A",
    },
    {
        "keywords": ("pcb", "carrier", "shield"),
        "name": "ForgeBot Custom 2-Layer FR4 Carrier PCB (5-board prototype run)",
        "category": "Custom Circuit Boards",
        "unit_price_usd": 5.00,
        "supplier": "JLCPCB / PCBWay",
        "part_number": "FORGEBOT-CARRIER-V1",
    },
]


def _match_cots_item(comp: dict[str, Any]) -> dict[str, Any] | None:
    text = f"{comp.get('id', '')} {comp.get('name', '')} {comp.get('role', '')}".lower()
    for item in COTS_CATALOG:
        if any(kw in text for kw in item["keywords"]):
            return item
    return None


def estimate_3d_print_and_fabrication_costs(
    design: dict[str, Any],
    cad_export: dict[str, Any] | None = None,
    mechatronics: dict[str, Any] | None = None,
    material_key: str = "pla",
    infill_percent: float = 25.0,
    bed_width_mm: float = 220.0,
    bed_depth_mm: float = 220.0,
    bed_height_mm: float = 250.0,
) -> dict[str, Any]:
    """Calculate physics-based 3D printing mass, print hours, bed fit, and complete hardware BOM costs."""
    material = PRINT_MATERIALS.get(material_key.lower(), PRINT_MATERIALS["pla"])
    density = material["density_g_cm3"]  # g/cm3
    cost_per_g = material["cost_per_kg_usd"] / 1000.0
    infill_ratio = max(0.10, min(1.0, infill_percent / 100.0))

    components = design.get("components", [])

    # Map OpenCASCADE solid volume (in mm3) from FreeCAD export report if available
    solid_volumes_map: dict[str, float] = {}
    if cad_export:
        report = cad_export.get("export_report") or {}
        val = report.get("solid_kernel_validation") or {}
        for cv in val.get("component_validity", []):
            if cv.get("component_id") and cv.get("volume_mm3"):
                solid_volumes_map[cv["component_id"]] = float(cv["volume_mm3"])

    printed_parts: list[dict[str, Any]] = []
    total_solid_vol_mm3 = 0.0
    total_print_vol_mm3 = 0.0
    total_mass_g = 0.0
    total_print_time_hours = 0.0
    oversized_parts: list[str] = []
    diagonal_parts: list[str] = []

    standard_bed_diag_mm = math.hypot(bed_width_mm, bed_depth_mm)

    for comp in components:
        comp_id = comp.get("id", "part")
        dims_m = comp.get("dimensions_m", [0.05, 0.05, 0.02])
        lx_mm = round(float(dims_m[0]) * 1000.0, 1)
        ly_mm = round(float(dims_m[1]) * 1000.0, 1)
        lz_mm = round(float(dims_m[2]) * 1000.0, 1)
        sorted_dims = sorted([lx_mm, ly_mm, lz_mm], reverse=True)
        max_dim = sorted_dims[0]
        mid_dim = sorted_dims[1]
        height_dim = sorted_dims[2]

        # Use exact OpenCASCADE volume if available, or compute geometric envelope volume
        geom = comp.get("geometry", "box")
        if comp_id in solid_volumes_map and solid_volumes_map[comp_id] > 0.0:
            solid_vol_mm3 = solid_volumes_map[comp_id]
        elif geom == "cylinder":
            radius = min(lx_mm, ly_mm) / 2.0
            solid_vol_mm3 = math.pi * (radius**2) * lz_mm
        elif geom == "sphere":
            radius = min(lx_mm, ly_mm, lz_mm) / 2.0
            solid_vol_mm3 = (4.0 / 3.0) * math.pi * (radius**3)
        else:  # box
            solid_vol_mm3 = lx_mm * ly_mm * lz_mm

        # Slicing Model: 3 perimeters shell + top/bottom skins + internal infill
        # Estimate surface area (mm2) for outer shell thickness
        if geom == "cylinder":
            radius = min(lx_mm, ly_mm) / 2.0
            surf_area_mm2 = 2.0 * math.pi * radius * (radius + lz_mm)
        elif geom == "sphere":
            radius = min(lx_mm, ly_mm, lz_mm) / 2.0
            surf_area_mm2 = 4.0 * math.pi * (radius**2)
        else:
            surf_area_mm2 = 2.0 * (lx_mm * ly_mm + lx_mm * lz_mm + ly_mm * lz_mm)

        shell_vol_mm3 = min(solid_vol_mm3 * 0.90, surf_area_mm2 * SHELL_THICKNESS_MM)
        core_vol_mm3 = max(0.0, solid_vol_mm3 - shell_vol_mm3)
        infill_vol_mm3 = core_vol_mm3 * infill_ratio
        part_print_vol_mm3 = shell_vol_mm3 + infill_vol_mm3

        # Mass (g) = volume (cm3) * density (g/cm3)
        part_mass_g = (part_print_vol_mm3 / 1000.0) * density
        part_filament_m = part_print_vol_mm3 / (math.pi * (1.75 / 2.0) ** 2 * 1000.0)
        part_material_cost_usd = part_mass_g * cost_per_g

        # Print Time (hours) based on volumetric hotend extrusion flow + layer change overhead
        layer_count = int(math.ceil(height_dim / LAYER_HEIGHT_MM))
        extrusion_time_s = part_print_vol_mm3 / HOTEND_VOLUMETRIC_FLOW_MM3_S
        layer_overhead_s = layer_count * 1.4  # Retraction, Z-hop, layer change
        part_time_hours = (extrusion_time_s + layer_overhead_s) / 3600.0

        # Bed Fit Verification
        fits_normal = max_dim <= bed_width_mm and mid_dim <= bed_depth_mm and height_dim <= bed_height_mm
        fits_diagonal = math.hypot(max_dim, mid_dim) <= standard_bed_diag_mm and height_dim <= bed_height_mm
        if fits_normal:
            fit_status = "fits_standard_bed"
            fit_label = f"✓ Fits {bed_width_mm:.0f}×{bed_depth_mm:.0f} mm bed"
        elif fits_diagonal:
            fit_status = "fits_diagonal"
            fit_label = f"⚠️ Requires 45° diagonal angle (hypotenuse {math.hypot(max_dim, mid_dim):.0f} mm)"
            diagonal_parts.append(comp_id)
        else:
            fit_status = "exceeds_bed"
            fit_label = f"❌ Exceeds standard bed ({max_dim:.0f} mm) — recommend splitting"
            oversized_parts.append(comp_id)

        part_record = {
            "component_id": comp_id,
            "name": comp.get("name", comp_id),
            "role": comp.get("role", "structural"),
            "geometry": geom,
            "bounding_box_mm": [lx_mm, ly_mm, lz_mm],
            "solid_volume_cm3": round(solid_vol_mm3 / 1000.0, 2),
            "printed_volume_cm3": round(part_print_vol_mm3 / 1000.0, 2),
            "filament_mass_g": round(part_mass_g, 1),
            "filament_length_m": round(part_filament_m, 2),
            "print_time_hours": round(part_time_hours, 2),
            "print_time_human": f"{int(part_time_hours)}h {int((part_time_hours % 1) * 60)}m",
            "material_cost_usd": round(part_material_cost_usd, 2),
            "bed_fit_status": fit_status,
            "bed_fit_label": fit_label,
            "is_opencascade_exact": comp_id in solid_volumes_map,
        }
        printed_parts.append(part_record)
        total_solid_vol_mm3 += solid_vol_mm3
        total_print_vol_mm3 += part_print_vol_mm3
        total_mass_g += part_mass_g
        total_print_time_hours += part_time_hours

    # 3D Printing Overall Totals
    total_filament_cost_usd = round(total_mass_g * cost_per_g, 2)
    total_energy_kwh = total_print_time_hours * PRINTER_POWER_KW
    total_energy_cost_usd = round(total_energy_kwh * ELECTRICITY_COST_KWH_USD, 2)
    spools_needed = int(math.ceil(total_mass_g / 1000.0))

    # Fasteners & Threaded Inserts (derived from assembly holes)
    m3_hole_count = 0
    if cad_export:
        man = cad_export.get("assembly_manifest") or {}
        for c in man.get("components", []):
            for face in c.get("interfaces", []):
                pat = face.get("hole_pattern") or {}
                m3_hole_count += len(pat.get("hole_centers_local_m", []))
    if m3_hole_count == 0:
        m3_hole_count = max(8, len(components) * 4)

    fastener_screws_cost_usd = round(m3_hole_count * 0.08, 2)  # M3x10 socket cap screws ($0.08 each)
    brass_inserts_cost_usd = round(m3_hole_count * 0.12, 2)  # M3 heat-set brass threaded inserts ($0.12 each)
    total_fasteners_cost_usd = round(fastener_screws_cost_usd + brass_inserts_cost_usd, 2)

    # COTS Hardware & Electronics Items
    cots_items: list[dict[str, Any]] = []
    seen_categories: set[str] = set()

    for comp in components:
        matched = _match_cots_item(comp)
        if matched and matched["name"] not in seen_categories:
            seen_categories.add(matched["name"])
            qty = 2 if any(w in comp.get("id", "").lower() for w in ("wheel", "motor", "wing", "leg", "servo")) else 1
            total_item_usd = round(matched["unit_price_usd"] * qty, 2)
            cots_items.append({
                "category": matched["category"],
                "name": matched["name"],
                "part_number": matched["part_number"],
                "supplier": matched["supplier"],
                "quantity": qty,
                "unit_price_usd": matched["unit_price_usd"],
                "total_price_usd": total_item_usd,
            })

    # Always ensure the foundational electronic kit is covered (SBC, Power, Sensors, Custom Carrier PCB)
    mandatory_cots = [
        {"name": "Raspberry Pi 4 Model B (4GB RAM) Quad-Core Robotics SBC", "qty": 1, "unit": 45.00, "supplier": "DigiKey / RPi", "cat": "Compute & Processing", "pn": "RPI4-MODBP-4GB"},
        {"name": "3S 11.1V 2200mAh 35C LiPo Battery Pack (XT60)", "qty": 1, "unit": 18.50, "supplier": "Amazon / Gens Ace", "cat": "Power System", "pn": "GA-B-35C-2200"},
        {"name": "Sony IMX219 8MP 160° Wide-Angle Robotics Camera Module", "qty": 1, "unit": 14.50, "supplier": "Arducam / Waveshare", "cat": "Perception Sensors", "pn": "B0393-IMX219"},
        {"name": "HC-SR04 Ultrasonic Distance Sensor Module (2–400 cm)", "qty": 2, "unit": 1.95, "supplier": "SparkFun / LCSC", "cat": "Perception Sensors", "pn": "SEN-15569"},
        {"name": "ForgeBot Custom 2-Layer FR4 Carrier PCB (5-board prototype run)", "qty": 1, "unit": 5.00, "supplier": "JLCPCB / PCBWay", "cat": "Custom Circuit Boards", "pn": "FORGEBOT-CARRIER-V1"},
        {"name": "Pre-Crimped 20-24 AWG Wiring Harness & JST-XH Connectors", "qty": 1, "unit": 6.80, "supplier": "Adafruit / Amazon", "cat": "Cables & Wiring", "pn": "HARNESS-JST-KIT"},
    ]
    for mand in mandatory_cots:
        if not any(item["name"] == mand["name"] for item in cots_items):
            cots_items.append({
                "category": mand["cat"],
                "name": mand["name"],
                "part_number": mand["pn"],
                "supplier": mand["supplier"],
                "quantity": mand["qty"],
                "unit_price_usd": mand["unit"],
                "total_price_usd": round(mand["qty"] * mand["unit"], 2),
            })

    total_cots_usd = round(sum(item["total_price_usd"] for item in cots_items), 2)
    grand_total_usd = round(total_filament_cost_usd + total_energy_cost_usd + total_fasteners_cost_usd + total_cots_usd, 2)

    return {
        "schema_version": "forgebot-fabrication-estimator/v1",
        "material": material,
        "infill_percent": infill_percent,
        "standard_bed_mm": [bed_width_mm, bed_depth_mm, bed_height_mm],
        "summary": {
            "total_printed_parts": len(printed_parts),
            "total_mass_grams": round(total_mass_g, 1),
            "total_mass_kg": round(total_mass_g / 1000.0, 3),
            "spools_1kg_required": spools_needed,
            "total_print_time_hours": round(total_print_time_hours, 1),
            "total_print_time_human": f"{int(total_print_time_hours)}h {int((total_print_time_hours % 1) * 60)}m",
            "material_filament_cost_usd": total_filament_cost_usd,
            "machine_energy_cost_usd": total_energy_cost_usd,
            "fasteners_and_inserts_cost_usd": total_fasteners_cost_usd,
            "fastener_screw_count": m3_hole_count,
            "threaded_insert_count": m3_hole_count,
            "cots_electronics_cost_usd": total_cots_usd,
            "grand_total_build_cost_usd": grand_total_usd,
            "bed_fit_summary": {
                "fits_standard_bed_count": len(printed_parts) - len(oversized_parts) - len(diagonal_parts),
                "diagonal_fit_count": len(diagonal_parts),
                "exceeds_bed_count": len(oversized_parts),
                "oversized_parts": oversized_parts,
            },
        },
        "printed_parts": printed_parts,
        "cots_bom": cots_items,
        "available_materials": list(PRINT_MATERIALS.values()),
    }
