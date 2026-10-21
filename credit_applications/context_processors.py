from .permissions import (
    can_approve_applications,
    can_manage_bank,
    can_process_applications,
    can_review_applications,
    can_submit_applications,
)


def permissions(request):
    return {
        "can_review_applications": can_review_applications(request.user),
        "can_approve_applications": can_approve_applications(request.user),
        "can_manage_bank": can_manage_bank(request.user),
        "can_process_applications": can_process_applications(request.user),
        "can_submit_applications": can_submit_applications(request.user),
    }
