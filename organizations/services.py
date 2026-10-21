from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from users.permissions import can_manage_bank

from .models import Organization, OrganizationAccessRequest, OrganizationMembership


def organization_for_submission(*, inn, name, user):
    """Организация, от имени которой человек вправе подать заявку.

    Возвращает пару (организация, запрос доступа): если членства ещё нет, вместо
    организации заводится запрос на подтверждение полномочий — ИНН сам по себе
    доступа не даёт. Вызывать внутри транзакции: строка организации блокируется.
    """
    organization = Organization.objects.select_for_update().filter(inn=inn).first()
    if organization is None:
        organization = Organization.objects.create(name=name, inn=inn)

    membership = OrganizationMembership.objects.filter(
        organization=organization, user=user, is_active=True
    ).first()
    if membership is None:
        access_request, _ = OrganizationAccessRequest.objects.update_or_create(
            organization=organization,
            user=user,
            defaults={
                "status": OrganizationAccessRequest.Status.PENDING,
                "reviewed_by": None,
                "review_note": "",
                "reviewed_at": None,
            },
        )
        return None, access_request
    if membership.role == OrganizationMembership.Role.VIEWER:
        raise PermissionDenied("У вас нет права отправлять заявки этой организации")
    if organization.verification_status != Organization.VerificationStatus.VERIFIED:
        raise ValidationError("Организация ещё не прошла подтверждение")
    return organization, None


@transaction.atomic
def review_organization_access(access_request, *, reviewer, approve, note=""):
    if not can_manage_bank(reviewer):
        raise PermissionDenied("Подтверждение организаций доступно только администратору банка")
    access_request = OrganizationAccessRequest.objects.select_for_update().select_related(
        "organization", "user"
    ).get(pk=access_request.pk)
    if access_request.status != OrganizationAccessRequest.Status.PENDING:
        raise ValidationError("Этот запрос уже обработан")

    access_request.reviewed_by = reviewer
    access_request.review_note = note.strip()
    access_request.reviewed_at = timezone.now()
    if approve:
        has_members = OrganizationMembership.objects.filter(
            organization=access_request.organization, is_active=True
        ).exists()
        OrganizationMembership.objects.update_or_create(
            organization=access_request.organization,
            user=access_request.user,
            defaults={
                "role": (
                    OrganizationMembership.Role.EDITOR
                    if has_members
                    else OrganizationMembership.Role.OWNER
                ),
                "is_active": True,
            },
        )
        access_request.status = OrganizationAccessRequest.Status.APPROVED
        access_request.organization.verification_status = Organization.VerificationStatus.VERIFIED
        access_request.organization.save(update_fields=("verification_status", "updated_at"))
    else:
        access_request.status = OrganizationAccessRequest.Status.REJECTED
        if not OrganizationMembership.objects.filter(
            organization=access_request.organization, is_active=True
        ).exists():
            access_request.organization.verification_status = Organization.VerificationStatus.NEEDS_REVIEW
            access_request.organization.save(update_fields=("verification_status", "updated_at"))
    access_request.save(
        update_fields=("status", "reviewed_by", "review_note", "reviewed_at")
    )
    return access_request
