from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from users.permissions import can_manage_bank, can_review_applications


@login_required
def dashboard(request):
    if can_manage_bank(request.user):
        return redirect("bank-admin-dashboard")
    if can_review_applications(request.user):
        return redirect("bank-queue")
    return redirect("business-cabinet")
