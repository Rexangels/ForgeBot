"""ForgeBot Phase 1 URL configuration."""

from django.urls import path

from core import views

urlpatterns = [
    path("api/v1/vision/analyze", views.analyze_scene, name="analyze-scene"),
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
