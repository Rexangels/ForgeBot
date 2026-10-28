"""Run the installed KiCad CLI to export a native PCB board as GLB."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


class KiCadExportError(RuntimeError):
    pass


def _find_kicad_cli() -> Path | None:
    configured = os.environ.get("KICAD_CLI_PATH")
    if configured and Path(configured).is_file():
        return Path(configured)
    on_path = shutil.which("kicad-cli") or shutil.which("kicad-cli.exe")
    if on_path:
        return Path(on_path)
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidates = list(program_files.glob("KiCad/*/bin/kicad-cli.exe"))
    return sorted(candidates, reverse=True)[0] if candidates else None


def _find_3d_model_dir(cli_path: Path) -> Path | None:
    configured = os.environ.get("KICAD10_3DMODEL_DIR") or os.environ.get("KICAD_3DMODEL_DIR")
    if configured and Path(configured).is_dir():
        return Path(configured)
    candidates = (
        cli_path.parent.parent / "share" / "kicad" / "3dmodels",
        cli_path.parent.parent.parent / "share" / "kicad" / "3dmodels",
    )
    return next((path for path in candidates if path.is_dir()), None)


def export_pcb_glb(board_text: str) -> bytes:
    if len(board_text.encode("utf-8")) > 2_000_000:
        raise KiCadExportError("PCB board data is too large to export.")
    if not board_text.lstrip().startswith("(kicad_pcb"):
        raise KiCadExportError("The saved PCB data is not a KiCad board.")

    cli_path = _find_kicad_cli()
    if cli_path is None:
        raise KiCadExportError("KiCad CLI is not installed or KICAD_CLI_PATH is not configured.")
    model_dir = _find_3d_model_dir(cli_path)
    if model_dir is None:
        raise KiCadExportError("KiCad 3D footprint models are not installed.")

    try:
        with tempfile.TemporaryDirectory(prefix="forgebot-kicad-") as temp_root:
            temp_path = Path(temp_root)
            board_path = temp_path / "forgebot_carrier.kicad_pcb"
            glb_path = temp_path / "forgebot_carrier.glb"
            board_path.write_text(board_text, encoding="utf-8")
            env = os.environ.copy()
            env["KICAD_CONFIG_HOME"] = str(temp_path / "config")
            env["KICAD_DOCUMENTS_HOME"] = str(temp_path / "documents")
            env["KICAD10_3DMODEL_DIR"] = str(model_dir)
            result = subprocess.run(
                [
                    str(cli_path), "pcb", "export", "glb", "--no-board-body", "--output", str(glb_path),
                    str(board_path),
                ],
                capture_output=True,
                text=True,
                timeout=90,
                env=env,
                check=False,
            )
            if result.returncode != 0 or not glb_path.is_file():
                detail = (result.stderr or result.stdout or "KiCad did not produce a GLB export.").strip()
                raise KiCadExportError(detail[-1200:])
            return glb_path.read_bytes()
    except subprocess.TimeoutExpired as exc:
        raise KiCadExportError("KiCad 3D export timed out. Try again or open the board in KiCad.") from exc
