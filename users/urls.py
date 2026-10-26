from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import StyledAuthenticationForm

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="users/login.html",
            authentication_form=StyledAuthenticationForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", views.register, name="register"),
]
