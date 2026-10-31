"""Local FreeCAD bridge for turning a reviewed robot spec into CAD files."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from django.conf import settings


class FreeCADError(RuntimeError):
    """Raised when the local FreeCAD process cannot complete an export."""


def _find_freecad() -> str | None:
    configured = os.environ.get("FORGEBOT_FREECAD_EXECUTABLE", "").strip()
    if configured and Path(configured).is_file():
        return configured
    on_path = (
        shutil.which("freecadcmd")
        or shutil.which("FreeCADCmd")
        or shutil.which("freecad")
        or shutil.which("FreeCAD")
    )
    if on_path:
        # If GUI binary was found on PATH, check if CLI counterpart exists next to it
        cmd_peer = Path(on_path).parent / "freecadcmd.exe"
        if cmd_peer.is_file():
            return str(cmd_peer)
        return on_path

    candidates = []
    # Search for headless console CLI first
    for name in ("freecadcmd.exe", "FreeCADCmd.exe", "freecad.exe", "FreeCAD.exe"):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.extend(Path(local_app_data).glob(f"Programs/FreeCAD*/bin/{name}"))
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            program_files = os.environ.get(variable)
            if program_files:
                candidates.extend(Path(program_files).glob(f"FreeCAD*/bin/{name}"))

    if not candidates:
        return None

    # Prefer headless freecadcmd.exe over GUI freecad.exe
    cmd_candidates = [c for c in candidates if "cmd" in c.name.lower()]
    if cmd_candidates:
        return str(sorted(cmd_candidates, reverse=True)[0])
    return str(sorted(candidates, reverse=True)[0])


def run_freecad_export(job_id: int, package: dict) -> dict:
    """Run the generated macro locally and package its CAD files as an artifact.

    ForgeBot waits until the macro writes its export report, then reads the files
    into the job artifact so the browser can offer downloads and load preview meshes.
    """
    executable = _find_freecad()
    if not executable:
        raise FreeCADError(
            "FreeCAD was not found. Set FORGEBOT_FREECAD_EXECUTABLE or run the included macro manually."
        )

    media_root = Path(settings.MEDIA_ROOT).resolve()
    run_dir = media_root / "forgebot" / "freecad" / f"job-{job_id}" / uuid.uuid4().hex[:10]
    run_dir.mkdir(parents=True, exist_ok=False)
    macro_path = run_dir / "build_step.FCMacro"
    macro_path.write_text(package["files"]["freecad/build_step.FCMacro"], encoding="utf-8")
    runner_path = run_dir / "forgebot_runner.FCMacro"
    error_path = run_dir / "freecad_error.txt"
    runner_path.write_text(
        "import os, traceback\n"
        f"macro_path = {str(macro_path)!r}\n"
        f"error_path = {str(error_path)!r}\n"
        "try:\n"
        "    with open(macro_path, 'r', encoding='utf-8') as source_file:\n"
        "        exec(compile(source_file.read(), macro_path, 'exec'), globals(), globals())\n"
        "except BaseException:\n"
        "    with open(error_path, 'w', encoding='utf-8') as error_file:\n"
        "        error_file.write(traceback.format_exc())\n"
        "    raise\n",
        encoding="utf-8",
    )
    output_dir = run_dir / "forgebot_step_export"
    report_path = output_dir / "export_report.json"
    process = None
    deadline = time.monotonic() + 180
    try:
        process = subprocess.Popen(
            [executable, str(runner_path)],
            cwd=str(run_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        while time.monotonic() < deadline:
            if report_path.is_file() and report_path.stat().st_size:
                break
            if error_path.is_file():
                raise FreeCADError(error_path.read_text(encoding="utf-8", errors="replace")[-4000:])
            exit_code = process.poll()
            if exit_code is not None:
                raise FreeCADError(f"FreeCAD closed before completing the export (exit code {exit_code}).")
            time.sleep(0.25)
        else:
            raise FreeCADError("FreeCAD did not finish the export within 180 seconds.")

        report = json.loads(report_path.read_text(encoding="utf-8"))
        step_path = Path(report["step_file"])
        native_path = Path(report["native_document"])
        manifest_path = Path(report["preview_manifest"])
        mesh_paths = sorted((output_dir / "parts").glob("*.stl"))
        if not step_path.is_file() or not native_path.is_file() or not manifest_path.is_file():
            raise FreeCADError("FreeCAD reported success but one or more required export files are missing.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        meshes = {f"parts/{path.name}": base64.b64encode(path.read_bytes()).decode("ascii") for path in mesh_paths}
        expected_meshes = {item["source_file"] for item in manifest.get("components", [])}
        if expected_meshes != set(meshes):
            raise FreeCADError("FreeCAD preview meshes do not match the component manifest.")

        result = {
            "schema_version": "forgebot-freecad-export/v1",
            "status": "completed",
            "freecad_version": report.get("freecad_version", "unknown"),
            "output_directory": str(run_dir.relative_to(media_root)),
            "step_file": {"name": step_path.name, "base64": base64.b64encode(step_path.read_bytes()).decode("ascii")},
            "native_document": {"name": native_path.name, "base64": base64.b64encode(native_path.read_bytes()).decode("ascii")},
            "assembly_manifest": manifest,
            "preview_meshes": meshes,
            "export_report": report,
        }
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        return result
    except (OSError, json.JSONDecodeError, KeyError, StopIteration) as exc:
        if process is not None and process.poll() is None:
            process.terminate()
        raise FreeCADError(f"Could not collect the FreeCAD export: {exc}") from exc
    except Exception:
        if process is not None and process.poll() is None:
            process.terminate()
        raise
