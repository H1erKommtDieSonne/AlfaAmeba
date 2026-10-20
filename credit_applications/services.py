from django.db import transaction
from django.utils import timezone

from .forms import ApplicationForm
from .models import Application, ApplicationVersion


@transaction.atomic
def submit_application(form: ApplicationForm, user) -> Application:
    data = form.cleaned_data
    application = Application.objects.create(
        organization_name=data["organization_name"],
        inn=data["inn"],
        created_by=user,
        status=Application.Status.DRAFT,
    )
    submitted_at = timezone.now()
    ApplicationVersion.objects.create(
        application=application,
        version_number=1,
        product=data["product"],
        amount=data["amount"],
        term_months=data["term_months"],
        purpose=data["purpose"],
        submitted_at=submitted_at,
        snapshot={
            "organization_name": data["organization_name"],
            "inn": data["inn"],
            "product_code": data["product"].code,
            "amount": str(data["amount"]),
            "term_months": data["term_months"],
            "purpose": data["purpose"],
            "version": 1,
            "submitted_at": submitted_at.isoformat(),
        },
    )
    application.status = Application.Status.SUBMITTED
    application.save(update_fields=("status", "updated_at"))
    return application

