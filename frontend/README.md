# ForgeBot frontend (React + Vite + Three.js)

Robotics workbench for mission setup, Meta-generated tabletop-arm proposals,
URDF and starter-control review, validation findings, and an interactive 3D
primitive assembly with orbit and joint-pose controls. The arm viewport is a
design preview, not parametric CAD or physics simulation. The rover route demo
remains available.

## Run it (two terminals)

Backend first:

```powershell
cd backend
..\.venv\Scripts\python manage.py migrate
..\.venv\Scripts\python manage.py check_meta_api
..\.venv\Scripts\python manage.py createsuperuser
..\.venv\Scripts\python manage.py runserver
```

Then the client (uses your normal `npm` cache; the `--cache` flag below is
only needed in locked-down shells):

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 in a browser. API calls go through the Vite
`/api` proxy to Django on :8000, so session sign-in works without CORS.

## What to click

1. Sign in with the superuser account.
2. Create a project, open it, choose **Tabletop robot arm**, and start a design run.
3. Review the generated specification and parts list, then approve to create the URDF and starter controls.
4. Inspect the 3D pose editor, URDF, code scaffold, validation report, and export manifest. Arm physics simulation is not implemented yet.
