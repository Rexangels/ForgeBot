# ForgeBot

ForgeBot is a prototype robotics design workbench for the Meta Global AI Developer Hackathon “Push the Frontier” track. It turns a mission brief into a reviewable robot proposal, description files, starter controls, and a 3D assembly preview.

## Project status

The app has a React + Three.js client and a Django backend. The tabletop-arm flow calls Meta Model API for a constrained JSON proposal, then builds and validates a URDF and starter control scaffold locally. The workbench parses the arm URDF into an articulated assembly, solves four-axis reach targets, and includes both a kinematic preview and a MuJoCo physics task. The physics task uses contacts, gravity, joint effort/speed limits, and a payload; it stores metrics and sampled states that can be replayed in the arm viewer. An optional deterministic search tunes the scripted position-servo gains. A research-humanoid workflow generates a kinematic URDF concept but does not yet have physics.

The project workspace also supports user-triggered still-image analysis: open the camera or select a workcell image, then ask Meta to describe visible objects, their visual state and relationships, image regions, uncertainty, and visible cautions. Click the four work-area corners to calibrate the image to the configured tabletop, review/edit a detected target, and carry it into the arm target planner. ForgeBot does not persist the image or analysis. Read [PROGRESS.md](PROGRESS.md) for implementation status and measurable milestones.

The same selected image can now be used to start an **image-to-robot concept** job. Describe the robot's task, give an approximate target height, and optionally add constraints. Meta proposes a small primitive component assembly for review. After approval, ForgeBot builds a connected URDF for the 3D viewer, separate OpenSCAD part modules, a positioned assembly, named inferred interfaces, and approximate assembly checks. If FreeCAD is installed on the same computer as Django, ForgeBot launches it automatically to create the native FCStd document, STEP assembly, per-component STL previews, and placement manifest; the CAD assembly view loads its preview parts and placements automatically. The downloadable ZIP and macro remain available for manual use. The image is sent to Meta only when you submit; ForgeBot does not retain it.

The image-to-robot output is an image-guided concept made from boxes, cylinders, and spheres, not recovered or manufacturing-ready CAD. Dimensions are estimates; proposed hole patterns are metadata only; materials, tolerances, and strength are not established. FreeCAD 1.1.4 creates native FCStd/STEP files and now runs OpenCASCADE solid validity and pairwise intersection checks; the AI can use these findings to propose a user-reviewed dimension edit that is rebuilt and rechecked. The robot model remains primitive concept geometry, and the repair proposal does not prove usefulness. The tabletop-arm physics model now uses contact friction and force-limited gripper actuators without an ideal grasp latch; benchmark results still apply only to estimated primitive geometry and inertias. Humanoid physics, perception-driven randomized training, real camera accuracy, hardware execution, durable queue workers, and engineering certification remain future work. All outputs need engineering review.

Vision currently analyzes one still image at a time. The first planar calibration uses four clicked table corners and fixed documented table/base dimensions; it has not been validated against physical measurements. Object localization from a bounding box is approximate, and a model's graspability assessment is only a hypothesis. Depth, object tracking, full collision checking, and safe real-world motion remain future work.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for component boundaries, validation and security requirements, open decisions, and the next delivery phases. The original project brief is preserved in `forge bot docs.rtf`.

## Run locally

1. Keep the Meta Model API key in `backend/.env` under `META_API` or `FORGEBOT_MODEL_API_KEY`. This file is git-ignored; never put the key in the frontend or commit it.
2. Start Django from `backend`: install `..\.venv\Scripts\python.exe -m pip install -r requirements.txt`, then run `..\.venv\Scripts\python.exe manage.py migrate`, `..\.venv\Scripts\python.exe manage.py check_meta_api`, `..\.venv\Scripts\python.exe manage.py createsuperuser`, and `..\.venv\Scripts\python.exe manage.py runserver`.
3. In another terminal, run `npm install` and `npm run dev` from `frontend`, then open `http://localhost:5173`.

Create a project, choose **Tabletop robot arm** or **Research humanoid**, describe the task, review the generated proposal, then inspect its URDF, control scaffold, validation report, and 3D model. For an approved tabletop arm, open **Simulation** to set pick/place coordinates in metres, run the MuJoCo physics task, optionally tune position-servo gains, and replay the sampled joint trajectory. If a run fails, Meta can propose one bounded parameter change; review and apply it, then rerun simulation to see whether it helped. Humanoid motion remains a kinematic concept.

Run backend acceptance checks from `backend` with `..\.venv\Scripts\python manage.py test core.tests`.

If the API reports that MuJoCo is missing, install `requirements.txt` into the exact Python interpreter that starts Django, then restart the Django process. Verify that interpreter with `python -c "import sys, mujoco; print(sys.executable, mujoco.__version__)"`; a successful check should show MuJoCo 3.x. Do not install into one Python environment and launch the server with another.

Generated designs and code require engineering review. A browser visualization or physics simulation does not establish that a physical robot is safe or ready to manufacture.
