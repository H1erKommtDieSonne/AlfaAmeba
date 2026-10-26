from .models import User


def can_review_applications(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.role in {User.Role.BANK_REVIEWER, User.Role.SENIOR_APPROVER}


def can_process_applications(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.role == User.Role.BANK_REVIEWER


def can_manage_bank(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.role == User.Role.BANK_ADMIN


def can_approve_applications(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.role == User.Role.SENIOR_APPROVER


def can_submit_applications(user) -> bool:
    if not user.is_authenticated:
        return False
    return user.role == User.Role.BUSINESS
