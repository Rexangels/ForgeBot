"""Export manifest for the reviewable prototype package."""

from __future__ import annotations

import hashlib
import json


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_manifest(
    *,
    input_revision: str,
    provider: str,
    model: str,
    artifact_versions: dict[str, str],
    validator_version: str,
    sim_config: dict,
) -> dict:
    """Assemble provenance so a result can be reproduced and reviewed."""
    return {
        "manifest_version": "export/v1",
        "input_revision": input_revision,
        "provider": provider,
        "model": model,
        "artifact_versions": artifact_versions,
        "validator_version": validator_version,
        "simulation": sim_config,
        "review_notice": (
            "Generated code and simulation results require engineering "
            "review; they are not proof of safety or buildability."
        ),
    }


def manifest_json(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True)
