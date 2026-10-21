from organizations.models import OrganizationMembership
from users.permissions import can_submit_applications

from .models import Application


def can_access_business_application(user, application: Application) -> bool:
    if not user.is_authenticated or not can_submit_applications(user):
        return False
    if application.created_by_id == user.pk:
        return True
    if application.organization_id is None:
        return False
    return OrganizationMembership.objects.filter(
        organization_id=application.organization_id,
        user=user,
        is_active=True,
    ).exists()


def can_edit_business_application(user, application: Application) -> bool:
    if not can_access_business_application(user, application):
        return False
    if application.status != Application.Status.NEEDS_REVISION:
        return False
    if application.created_by_id == user.pk:
        return True
    return OrganizationMembership.objects.filter(
        organization_id=application.organization_id,
        user=user,
        is_active=True,
        role__in=[
            OrganizationMembership.Role.OWNER,
            OrganizationMembership.Role.EDITOR,
            OrganizationMembership.Role.SUBMITTER,
        ],
    ).exists()
