# ForgeBot

ForgeBot is a prototype robotics design workbench for the Meta Global AI Developer Hackathon “Push the Frontier” track. It turns a mission brief into a reviewable robot proposal, description files, starter controls, and a 3D assembly preview.

## Project status

The app has a React + Three.js client and a Django backend. The tabletop-arm flow calls Meta Model API for a constrained JSON proposal, then builds and validates a URDF and starter control scaffold locally. The workbench parses the generated arm URDF into an articulated assembly, solves four-axis reach targets, and previews a velocity-limited pick-and-place sequence with a simplified tabletop-clearance check. The rover demo remains available with its deterministic route check.

The project workspace also supports user-triggered still-image analysis: open the camera or select a workcell image, then ask Meta to describe visible objects, their visual state and relationships, image regions, uncertainty, and visible cautions. Click the four work-area corners to calibrate the image to the configured tabletop, review/edit a detected target, and carry it into the arm target planner. ForgeBot does not persist the image or analysis. Read [PROGRESS.md](PROGRESS.md) for implementation status and measurable milestones.

This is an early prototype: arm physics simulation, parametric solid CAD, mesh-visual URDF support, full self/object collision checking, physically validated camera localization, hardware execution, durable queue workers, and engineering certification are not implemented. Model proposals, generated files, and the 3D preview need engineering review.

Vision currently analyzes one still image at a time. The first planar calibration uses four clicked table corners and fixed documented table/base dimensions; it has not been validated against physical measurements. Object localization from a bounding box is approximate, and a model's graspability assessment is only a hypothesis. Depth, object tracking, full collision checking, and safe real-world motion remain future work.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for component boundaries, validation and security requirements, open decisions, and the next delivery phases. The original project brief is preserved in `forge bot docs.rtf`.

## Run locally

1. Keep the Meta Model API key in `backend/.env` under `META_API` or `FORGEBOT_MODEL_API_KEY`. This file is git-ignored; never put the key in the frontend or commit it.
2. Start Django from `backend` with `..\.venv\Scripts\python manage.py migrate`, then `..\.venv\Scripts\python manage.py check_meta_api`, `..\.venv\Scripts\python manage.py createsuperuser`, and `..\.venv\Scripts\python manage.py runserver`.
3. In another terminal, run `npm install` and `npm run dev` from `frontend`, then open `http://localhost:5173`.

Create a project, choose **Tabletop robot arm**, describe the task, review the generated proposal, then inspect the URDF, control scaffold, validation report, and 3D joint-pose editor.

Generated designs and code require engineering review. A browser visualization or physics simulation does not establish that a physical robot is safe or ready to manufacture.
