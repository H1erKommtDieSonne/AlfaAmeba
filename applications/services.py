from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from documents.services import save_documents_and_enqueue
from organizations.models import OrganizationAccessRequest, OrganizationMembership
from organizations.services import organization_for_submission
from users.models import User
from users.permissions import can_approve_applications, can_review_applications

from .forms import ApplicationForm, ReviewActionForm
from .models import (
    Application,
    ApplicationComment,
    ApplicationEvent,
    ApplicationVersion,
)


def _snapshot(data, *, version_number, submitted_at):
    return {
        "organization_name": data["organization_name"],
        "inn": data["inn"],
        "product_code": data["product"].code,
        "amount": str(data["amount"]),
        "term_months": data["term_months"],
        "purpose": data["purpose"],
        "needs": {key: data.get(key) for key in ("need", "gap_kind", "debt_kind", "collateral")},
        "recommendation": data["recommendation"],
        "checklist": data["checklist"],
        "version": version_number,
        "submitted_at": submitted_at.isoformat(),
    }


@transaction.atomic
def submit_application(form: ApplicationForm, user) -> Application | OrganizationAccessRequest:
    data = form.cleaned_data
    organization, access_request = organization_for_submission(
        inn=data["inn"], name=data["organization_name"], user=user
    )
    if access_request is not None:
        return access_request
    submitted_at = timezone.now()
    application = Application.objects.create(
        organization=organization,
        organization_name=organization.name,
        inn=organization.inn,
        created_by=user,
        status=Application.Status.SUBMITTED,
        submitted_at=submitted_at,
    )
    version = ApplicationVersion.objects.create(
        application=application,
        version_number=1,
        product=data["product"],
        amount=data["amount"],
        term_months=data["term_months"],
        purpose=data["purpose"],
        submitted_at=submitted_at,
        snapshot=_snapshot(data, version_number=1, submitted_at=submitted_at),
    )
    save_documents_and_enqueue(version, data["document_uploads"], data["retained_documents"])
    ApplicationEvent.objects.create(
        application=application,
        actor=user,
        event_type=ApplicationEvent.Type.SUBMITTED,
        to_status=Application.Status.SUBMITTED,
        message="Первая версия заявки отправлена в банк",
        metadata={"version": 1},
    )
    return application


@transaction.atomic
def submit_revision(application: Application, form: ApplicationForm, user) -> ApplicationVersion:
    application = Application.objects.select_for_update().get(pk=application.pk)
    if application.status != Application.Status.NEEDS_REVISION:
        raise ValidationError("Эта заявка сейчас не ожидает доработки")
    if form.cleaned_data["inn"] != application.inn:
        raise ValidationError("ИНН организации нельзя изменить при доработке заявки")

    membership_exists = OrganizationMembership.objects.filter(
        organization=application.organization,
        user=user,
        is_active=True,
        role__in=[
            OrganizationMembership.Role.OWNER,
            OrganizationMembership.Role.EDITOR,
            OrganizationMembership.Role.SUBMITTER,
        ],
    ).exists()
    if application.created_by_id != user.pk and not membership_exists:
        raise PermissionDenied("У вас нет права дорабатывать эту заявку")

    previous_status = application.status
    version_number = application.current_version + 1
    submitted_at = timezone.now()
    data = form.cleaned_data
    version = ApplicationVersion.objects.create(
        application=application,
        version_number=version_number,
        product=data["product"],
        amount=data["amount"],
        term_months=data["term_months"],
        purpose=data["purpose"],
        submitted_at=submitted_at,
        snapshot=_snapshot(data, version_number=version_number, submitted_at=submitted_at),
    )
    save_documents_and_enqueue(version, data["document_uploads"], data["retained_documents"])
    application.current_version = version_number
    application.status = Application.Status.SUBMITTED
    application.submitted_at = submitted_at
    application.decided_at = None
    application.save(
        update_fields=("current_version", "status", "submitted_at", "decided_at", "updated_at")
    )
    ApplicationEvent.objects.create(
        application=application,
        actor=user,
        event_type=ApplicationEvent.Type.REVISION_SUBMITTED,
        from_status=previous_status,
        to_status=Application.Status.SUBMITTED,
        message=f"Отправлена версия №{version_number}",
        metadata={"version": version_number},
    )
    return version


@transaction.atomic
def add_comment(application: Application, *, author, text, audience) -> ApplicationComment:
    application = Application.objects.select_for_update().get(pk=application.pk)
    if not can_review_applications(author):
        raise PermissionDenied("Комментарии банка доступны только сотрудникам")
    comment = ApplicationComment.objects.create(
        application=application,
        author=author,
        text=text.strip(),
        audience=audience,
    )
    ApplicationEvent.objects.create(
        application=application,
        actor=author,
        event_type=ApplicationEvent.Type.COMMENT_ADDED,
        message=(
            "Добавлен комментарий клиенту"
            if audience == ApplicationComment.Audience.CLIENT
            else "Добавлен внутренний комментарий"
        ),
        metadata={"audience": audience, "comment_id": str(comment.pk)},
    )
    return comment


@transaction.atomic
def perform_review_action(application: Application, *, actor, action, note="") -> Application:
    if not can_review_applications(actor):
        raise PermissionDenied("Действие доступно только сотрудникам банка")
    application = Application.objects.select_for_update().get(pk=application.pk)
    old_status = application.status
    is_reviewer = actor.is_superuser or actor.role == User.Role.BANK_REVIEWER

    if (
        action == ReviewActionForm.ACTION_TAKE
        and old_status == Application.Status.SUBMITTED
        and is_reviewer
    ):
        application.assigned_to = actor
        new_status = Application.Status.IN_REVIEW
    elif (
        action == ReviewActionForm.ACTION_REQUEST_REVISION
        and old_status == Application.Status.IN_REVIEW
        and is_reviewer
    ):
        new_status = Application.Status.NEEDS_REVISION
    elif (
        action == ReviewActionForm.ACTION_SEND_TO_APPROVAL
        and old_status == Application.Status.IN_REVIEW
        and is_reviewer
    ):
        new_status = Application.Status.PENDING_APPROVAL
    elif action == ReviewActionForm.ACTION_APPROVE and old_status == Application.Status.PENDING_APPROVAL:
        if not can_approve_applications(actor):
            raise PermissionDenied("Одобрение доступно только старшему согласующему")
        new_status = Application.Status.APPROVED
    elif action == ReviewActionForm.ACTION_DECLINE and old_status == Application.Status.PENDING_APPROVAL:
        if not can_approve_applications(actor):
            raise PermissionDenied("Итоговое решение доступно только старшему согласующему")
        new_status = Application.Status.DECLINED
    elif action == ReviewActionForm.ACTION_RETURN_TO_REVIEW and old_status == Application.Status.PENDING_APPROVAL:
        if not can_approve_applications(actor):
            raise PermissionDenied("Возврат доступен только старшему согласующему")
        new_status = Application.Status.IN_REVIEW
    else:
        raise ValidationError("Это действие недоступно для текущего статуса заявки")

    if old_status == Application.Status.IN_REVIEW and is_reviewer:
        if application.assigned_to_id not in {None, actor.pk}:
            raise PermissionDenied("Заявка назначена другому сотруднику")
        application.assigned_to = actor

    application.status = new_status
    application.decided_at = (
        timezone.now()
        if new_status in {Application.Status.APPROVED, Application.Status.DECLINED}
        else None
    )
    application.save(update_fields=("assigned_to", "status", "decided_at", "updated_at"))

    if note.strip():
        audience = (
            ApplicationComment.Audience.INTERNAL
            if action in {
                ReviewActionForm.ACTION_SEND_TO_APPROVAL,
                ReviewActionForm.ACTION_RETURN_TO_REVIEW,
            }
            else ApplicationComment.Audience.CLIENT
        )
        ApplicationComment.objects.create(
            application=application,
            author=actor,
            audience=audience,
            text=note.strip(),
        )

    ApplicationEvent.objects.create(
        application=application,
        actor=actor,
        event_type=(
            ApplicationEvent.Type.ASSIGNED
            if action == ReviewActionForm.ACTION_TAKE
            else ApplicationEvent.Type.STATUS_CHANGED
        ),
        from_status=old_status,
        to_status=new_status,
        message=note.strip(),
        metadata={"action": action},
    )
    return application
