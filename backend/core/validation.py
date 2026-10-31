"""Deterministic artifact validators.

Validators return machine-readable findings; they never silently repair
user-approved inputs. Findings carry severity, artifact path, and guidance.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import xml.etree.ElementTree as ET

SEVERITY_BLOCKER = "blocker"
SEVERITY_WARNING = "warning"

SUPPORTED_GEOMETRY = frozenset({"box", "cylinder", "sphere", "mesh"})

# Generated starter code must stay reviewable: no execution or exfiltration.
FORBIDDEN_CODE_PATTERNS = (
    "os.system",
    "subprocess",
    "eval(",
    "exec(",
    "socket",
    "urllib",
    "requests",
)


@dataclass
class Finding:
    severity: str
    artifact_path: str
    message: str
    remediation: str = ""


def _num(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def validate_urdf(urdf_text: str, artifact_path: str = "rover.urdf") -> list[Finding]:
    """Parse and validate a URDF document deterministically."""
    findings: list[Finding] = []
    try:
        root = ET.fromstring(urdf_text)
    except ET.ParseError as exc:
        return [
            Finding(
                SEVERITY_BLOCKER,
                artifact_path,
                f"URDF XML does not parse: {exc}",
                "Fix XML syntax before simulation or export.",
            )
        ]
    if root.tag != "robot":
        findings.append(
            Finding(
                SEVERITY_BLOCKER,
                artifact_path,
                "URDF root element must be <robot>.",
                "Wrap the description in a single <robot> element.",
            )
        )
        return findings

    links = [l.get("name", "") for l in root.findall("link")]
    if len(links) != len(set(links)) or "" in links:
        findings.append(
            Finding(
                SEVERITY_BLOCKER,
                artifact_path,
                "Link names must be unique and non-empty.",
                "Rename duplicated or unnamed links.",
            )
        )
    joints = root.findall("joint")
    joint_names = [j.get("name", "") for j in joints]
    if len(joint_names) != len(set(joint_names)) or "" in joint_names:
        findings.append(
            Finding(
                SEVERITY_BLOCKER,
                artifact_path,
                "Joint names must be unique and non-empty.",
                "Rename duplicated or unnamed joints.",
            )
        )

    link_set = set(links)
    parents: dict[str, str] = {}
    children: set[str] = set()
    for joint in joints:
        parent = joint.find("parent")
        child = joint.find("child")
        pname = parent.get("link", "") if parent is not None else ""
        cname = child.get("link", "") if child is not None else ""
        if pname not in link_set or cname not in link_set:
            findings.append(
                Finding(
                    SEVERITY_BLOCKER,
                    artifact_path,
                    f"Joint {joint.get('name', '?')!r} references unknown links.",
                    "Point parent/child at declared link names.",
                )
            )
        if cname in parents:
            findings.append(
                Finding(
                    SEVERITY_BLOCKER,
                    artifact_path,
                    f"Link {cname!r} has more than one parent joint.",
                    "A URDF tree needs exactly one parent per non-root link.",
                )
            )
        parents[cname] = pname
        children.add(cname)
        jtype = joint.get("type")
        limit = joint.find("limit")
        if jtype != "fixed" and limit is None:
            findings.append(
                Finding(
                    SEVERITY_BLOCKER,
                    artifact_path,
                    f"Joint {joint.get('name', '?')!r} needs limits.",
                    "Add lower/upper/effort/velocity limits.",
                )
            )
        elif limit is not None:
            effort, vel = _num(limit.get("effort")), _num(limit.get("velocity"))
            if jtype != "continuous":
                lower, upper = _num(limit.get("lower")), _num(limit.get("upper"))
                if lower is None or upper is None or lower >= upper:
                    findings.append(
                        Finding(
                            SEVERITY_BLOCKER,
                            artifact_path,
                            f"Joint {joint.get('name', '?')!r} has invalid limits.",
                            "Use finite lower < upper values.",
                        )
                    )
            for label, val in (("effort", effort), ("velocity", vel)):
                if val is None or not math.isfinite(val) or val <= 0:
                    findings.append(
                        Finding(
                            SEVERITY_BLOCKER,
                            artifact_path,
                            f"Joint {joint.get('name', '?')!r} has invalid {label}.",
                            "Use a finite positive value.",
                        )
                    )

    # Connected tree: every link reachable from the single root.
    roots = [l for l in links if l not in children]
    if len(roots) != 1:
        findings.append(
            Finding(
                SEVERITY_BLOCKER,
                artifact_path,
                "Kinematic tree must have exactly one root link.",
                "Connect detached links with joints.",
            )
        )

    for link in root.findall("link"):
        lname = link.get("name", "?")
        for geo in link.iter("geometry"):
            kinds = {c.tag for c in geo}
            if not kinds & SUPPORTED_GEOMETRY:
                findings.append(
                    Finding(
                        SEVERITY_WARNING,
                        artifact_path,
                        f"Link {lname!r} uses unsupported geometry.",
                        "Use box, cylinder, sphere, or mesh.",
                    )
                )
        for inertial in link.findall("inertial"):
            mass_el = inertial.find("mass")
            m = _num(mass_el.get("value")) if mass_el is not None else None
            if m is None or not math.isfinite(m) or m <= 0:
                findings.append(
                    Finding(
                        SEVERITY_BLOCKER,
                        artifact_path,
                        f"Link {lname!r} needs a positive finite mass.",
                        "Set inertial/mass to a positive value in kg.",
                    )
                )
            for axis in ("ixx", "iyy", "izz"):
                el = inertial.find("inertia")
                v = _num(el.get(axis)) if el is not None else None
                if v is None or not math.isfinite(v) or v <= 0:
                    findings.append(
                        Finding(
                            SEVERITY_BLOCKER,
                            artifact_path,
                            f"Link {lname!r} needs finite positive inertia {axis}.",
                            "Set realistic inertia values.",
                        )
                    )
    return findings


def check_generated_code(
    code: str, artifact_path: str = "starter.py"
) -> list[Finding]:
    """Static checks for generated starter code (never executed here)."""
    findings = []
    lowered = code.lower()
    for pattern in FORBIDDEN_CODE_PATTERNS:
        if pattern in lowered:
            findings.append(
                Finding(
                    SEVERITY_BLOCKER,
                    artifact_path,
                    f"Generated code contains forbidden pattern {pattern!r}.",
                    "Remove execution/network access from generated code.",
                )
            )
    if "todo" in lowered or "placeholder" in lowered:
        findings.append(
            Finding(
                SEVERITY_WARNING,
                artifact_path,
                "Generated code contains placeholders.",
                "Fill in TODO sections before hardware use.",
            )
        )
    return findings
