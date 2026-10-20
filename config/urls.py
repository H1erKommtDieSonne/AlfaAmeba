from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from credit_applications.forms import StyledAuthenticationForm
from credit_applications.views import register

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            authentication_form=StyledAuthenticationForm,
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", register, name="register"),
    path("", include("credit_applications.urls")),
]

