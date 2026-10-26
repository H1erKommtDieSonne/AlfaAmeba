from django.urls import path

from . import views

# Pull-API аналитики. Контракт с воркером аналитики; токен отдельный от LLM.
urlpatterns = [
    path("api/v1/analytics/jobs/claim/", views.claim_job, name="analytics-job-claim"),
    path(
        "api/v1/analytics/jobs/<uuid:run_id>/complete/",
        views.complete_job_view,
        name="analytics-job-complete",
    ),
    path(
        "api/v1/analytics/jobs/<uuid:run_id>/fail/",
        views.fail_job_view,
        name="analytics-job-fail",
    ),
]
