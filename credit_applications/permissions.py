from .models import UserProfile


def can_review_applications(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        return user.profile.role in {
            UserProfile.Role.BANK_EMPLOYEE,
            UserProfile.Role.BANK_ADMIN,
        }
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


def can_submit_applications(user) -> bool:
    if not user.is_authenticated:
        return False
    try:
        return user.profile.role == UserProfile.Role.BUSINESS
    except UserProfile.DoesNotExist:
        return False

