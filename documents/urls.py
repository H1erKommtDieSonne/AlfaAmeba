from django.urls import path

from . import views

# Pull-API для LLM-воркера. Адреса — контракт с ml/queue_client.py и правилом в infra/nginx.conf:
# меняются только вместе с ними.
urlpatterns = [
    path("documents/<uuid:document_id>/download/", views.download_document, name="document-download"),
    path("api/v1/llm/jobs/<uuid:job_id>/renew/", views.renew_job_view, name="llm-job-renew"),
    path("api/v1/llm/jobs/claim/", views.claim_job, name="llm-job-claim"),
    path(
        "api/v1/llm/jobs/<uuid:job_id>/documents/<uuid:document_id>/",
        views.download_job_document,
        name="llm-job-document",
    ),
    path(
        "api/v1/llm/jobs/<uuid:job_id>/complete/",
        views.complete_job_view,
        name="llm-job-complete",
    ),
    path(
        "api/v1/llm/jobs/<uuid:job_id>/fail/",
        views.fail_job_view,
        name="llm-job-fail",
    ),
]
