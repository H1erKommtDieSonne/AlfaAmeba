from django import forms
from django.contrib.auth import forms as auth_forms

from core.forms import INPUT_CLASS

from .models import User


class StyledAuthenticationForm(auth_forms.AuthenticationForm):
    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"class": INPUT_CLASS, "autocomplete": "email", "autofocus": True}),
    )
    password = forms.CharField(
        label="Пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "autocomplete": "current-password"}),
    )


class RegistrationForm(auth_forms.UserCreationForm):
    first_name = forms.CharField(
        label="Имя",
        max_length=150,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "autocomplete": "given-name"}),
    )
    last_name = forms.CharField(
        label="Фамилия",
        max_length=150,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "autocomplete": "family-name"}),
    )
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"class": INPUT_CLASS, "autocomplete": "email"}),
    )
    password1 = forms.CharField(
        label="Пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Повторите пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "autocomplete": "new-password"}),
    )

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Пользователь с таким email уже зарегистрирован")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        # Обычная регистрация всегда создаёт представителя бизнеса; банковские роли назначаются вручную.
        user.role = User.Role.BUSINESS
        if commit:
            user.save()
        return user


class AdminUserCreationForm(auth_forms.AdminUserCreationForm):
    class Meta:
        model = User
        fields = ("email",)


class AdminUserChangeForm(auth_forms.UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"
