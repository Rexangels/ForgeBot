# ForgeBot backend (Django prototype)

## Local demo (no Docker)

```powershell
cd backend
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py check_meta_api
..\.venv\Scripts\python.exe manage.py test
..\.venv\Scripts\python.exe manage.py runserver
```

Django loads the local `backend/.env`. Set the Meta Model API key as `META_API`
or `FORGEBOT_MODEL_API_KEY`; it stays on the server. Arm jobs use Muse Spark to
propose a structured design, then build URDF and starter controls locally. The
arm path currently uses an in-process development thread, not a durable queue.
The rover demo retains its deterministic route-check pipeline.

If the API reports that MuJoCo is missing, install `requirements.txt` into the
exact Python interpreter that starts Django, then restart that process. Verify
with `python -c "import sys, mujoco; print(sys.executable, mujoco.__version__)"`.

## Multi-service shape

Copy `.env.example` to `.env`, then `docker compose up --build`.
PostgreSQL, Redis, and MinIO come from Compose; the API, background
worker, and isolated simulation worker run as separate services.
