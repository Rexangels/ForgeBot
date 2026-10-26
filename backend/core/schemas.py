"""Versioned, typed artifact schemas for the workflow handoff.

Free-form model prose is never the source of truth; these dict schemas are.
"""

from __future__ import annotations

MISSION_SCHEMA_VERSION = "mission/v1"
SPEC_SCHEMA_VERSION = "rover-spec/v1"
BOM_SCHEMA_VERSION = "bom/v1"

MISSION_REQUIRED = ("task", "units", "environment")
SPEC_REQUIRED = ("robot_class", "links", "joints", "assumptions")
SUPPORTED_ROBOT_CLASSES = ("differential-drive-rover", "tabletop_robot_arm")


def validate_mission(payload: dict) -> list[str]:
    """Return a list of blocking errors (empty means valid)."""
    errors = []
    if not isinstance(payload, dict):
        return ["mission must be an object"]
    for key in MISSION_REQUIRED:
        if not payload.get(key):
            errors.append(f"mission.{key} is required")
    if payload.get("units") not in (None, "si", "metric", "imperial"):
        errors.append("mission.units must be si, metric, or imperial")
    robot_class = payload.get("robot_class", "differential-drive-rover")
    if robot_class not in SUPPORTED_ROBOT_CLASSES:
        errors.append(f"mission.robot_class must be one of {', '.join(SUPPORTED_ROBOT_CLASSES)}")
    return errors


def validate_spec(payload: dict) -> list[str]:
    errors = []
    if not isinstance(payload, dict):
        return ["spec must be an object"]
    if payload.get("schema_version") != SPEC_SCHEMA_VERSION:
        errors.append(f"spec.schema_version must be {SPEC_SCHEMA_VERSION!r}")
    if payload.get("robot_class") != "differential-drive-rover":
        errors.append("Phase 1 supports only robot_class differential-drive-rover")
    for key in ("links", "joints"):
        if not isinstance(payload.get(key), list) or not payload.get(key):
            errors.append(f"spec.{key} must be a non-empty list")
    if not isinstance(payload.get("assumptions"), list):
        errors.append("spec.assumptions must be a list")
    return errors


def validate_bom(payload: dict) -> list[str]:
    errors = []
    if not isinstance(payload, dict):
        return ["bom must be an object"]
    if payload.get("schema_version") != BOM_SCHEMA_VERSION:
        errors.append(f"bom.schema_version must be {BOM_SCHEMA_VERSION!r}")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        errors.append("bom.items must be a non-empty list")
        return errors
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"bom.items[{i}] must be an object")
            continue
        if not item.get("name"):
            errors.append(f"bom.items[{i}].name is required")
        if item.get("availability", "verified") not in (
            "verified",
            "unverified",
            "unavailable",
        ):
            errors.append(f"bom.items[{i}].availability is unknown")
    return errors
