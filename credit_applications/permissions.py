from .models import Application, OrganizationMembership, UserProfile


def can_review_applications(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        return user.profile.role in {
            UserProfile.Role.BANK_REVIEWER,
            UserProfile.Role.SENIOR_APPROVER,
        }
    except UserProfile.DoesNotExist:
        return False


def can_process_applications(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        return user.profile.role == UserProfile.Role.BANK_REVIEWER
    except UserProfile.DoesNotExist:
        return False


def can_manage_bank(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        return user.profile.role == UserProfile.Role.BANK_ADMIN
    except UserProfile.DoesNotExist:
        return False


def can_approve_applications(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        return user.profile.role == UserProfile.Role.SENIOR_APPROVER
    except UserProfile.DoesNotExist:
        return False


def can_submit_applications(user) -> bool:
    if not user.is_authenticated:
        return False
    try:
        return user.profile.role == UserProfile.Role.BUSINESS
    except UserProfile.DoesNotExist:
        return False


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

