"""ForgeBot Phase 1 URL configuration."""

from django.urls import path

from core import views

urlpatterns = [
    path("api/v1/vision/analyze", views.analyze_scene, name="analyze-scene"),
    path("api/v1/ops/summary", views.ops_summary, name="ops-summary"),
    path("api/v1/vision/track", views.track_detections, name="track-detections"),
    path("api/v1/projects/<int:project_id>/calibrations", views.save_calibration, name="save-calibration"),
    path("api/v1/projects/<int:project_id>/calibrations/latest", views.latest_calibration, name="latest-calibration"),
    path("api/v1/projects/<int:project_id>/image-design", views.create_image_design, name="image-design"),
    path("api/v1/auth/csrf", views.csrf_token, name="csrf"),
    path("api/v1/auth/login", views.login_view, name="login"),
    path("api/v1/auth/logout", views.logout_view, name="logout"),
    path("api/v1/auth/me", views.me_view, name="me"),
    path("api/v1/projects/", views.create_project, name="create-project"),
    path("api/v1/projects/list", views.list_projects, name="list-projects"),
    path(
        "api/v1/projects/<int:project_id>/jobs",
        views.create_job,
        name="create-job",
    ),
    path(
        "api/v1/projects/<int:project_id>/jobs/list",
        views.list_jobs,
        name="list-jobs",
    ),
    path(
        "api/v1/projects/<int:project_id>/demo-jobs",
        views.create_demo_job,
        name="create-demo-job",
    ),
    path(
        "api/v1/jobs/<int:job_id>/approve",
        views.approve_job,
        name="approve-job",
    ),
    path(
        "api/v1/jobs/<int:job_id>/rebuild-cad",
        views.rebuild_cad_package,
        name="rebuild-cad-package",
    ),
    path(
        "api/v1/jobs/<int:job_id>/edit-cad-sketches",
        views.edit_cad_sketches,
        name="edit-cad-sketches",
    ),
    path(
        "api/v1/jobs/<int:job_id>/propose-cad-sketch-edit",
        views.propose_cad_sketch_edit,
        name="propose-cad-sketch-edit",
    ),
    path(
        "api/v1/jobs/<int:job_id>/propose-design-revision",
        views.propose_design_revision,
        name="propose-design-revision",
    ),
    path(
        "api/v1/jobs/<int:job_id>/apply-design-revision",
        views.apply_design_revision,
        name="apply-design-revision",
    ),
    path(
        "api/v1/jobs/<int:job_id>/simulate-humanoid",
        views.simulate_humanoid,
        name="simulate-humanoid",
    ),
    path(
        "api/v1/jobs/<int:job_id>/inspect-component",
        views.inspect_design_component,
        name="inspect-design-component",
    ),
    path(
        "api/v1/jobs/<int:job_id>/ask-pcb-component",
        views.ask_pcb_component,
        name="ask-pcb-component",
    ),
    path(
        "api/v1/jobs/<int:job_id>/export-pcb-3d",
        views.export_pcb_3d,
        name="export-pcb-3d",
    ),
    path(
        "api/v1/jobs/<int:job_id>/simulate-arm",
        views.simulate_tabletop_arm,
        name="simulate-tabletop-arm",
    ),
    path(
        "api/v1/jobs/<int:job_id>/benchmark-arm-designs",
        views.benchmark_project_arm_designs,
        name="benchmark-project-arm-designs",
    ),
    path(
        "api/v1/jobs/<int:job_id>/arm-simulation-defaults",
        views.tabletop_arm_simulation_defaults,
        name="tabletop-arm-simulation-defaults",
    ),
    path(
        "api/v1/jobs/<int:job_id>/propose-arm-repair",
        views.propose_arm_simulation_repair,
        name="propose-arm-simulation-repair",
    ),
    path(
        "api/v1/jobs/<int:job_id>/apply-arm-repair",
        views.apply_arm_simulation_repair,
        name="apply-arm-simulation-repair",
    ),
    path("api/v1/jobs/<int:job_id>", views.get_job, name="get-job"),
    path("api/v1/jobs/<int:job_id>/cancel", views.cancel_job, name="cancel-job"),
    path(
        "api/v1/jobs/<int:job_id>/events", views.job_events, name="job-events"
    ),
    path(
        "api/v1/artifacts/<int:artifact_id>",
        views.get_artifact,
        name="get-artifact",
    ),
]
