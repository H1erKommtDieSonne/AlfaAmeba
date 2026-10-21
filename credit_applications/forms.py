from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import ApplicationComment, Product


INPUT_CLASS = (
    "mt-2 h-12 w-full rounded-[10px] border border-transparent bg-[#f2f3f5] px-4 "
    "text-[14px] text-[#0b1220] outline-none transition placeholder:text-[#8b95a5] "
    "hover:bg-[#ebeef1] focus:border-[#ef3124] focus:bg-white focus:ring-2 focus:ring-[#ef3124]/10"
)


class StyledAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"class": INPUT_CLASS, "autocomplete": "email", "autofocus": True}),
    )
    password = forms.CharField(
        label="Пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": INPUT_CLASS, "autocomplete": "current-password"}),
    )


class RegistrationForm(UserCreationForm):
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
        model = get_user_model()
        fields = ("first_name", "last_name", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(username__iexact=email).exists():
            raise forms.ValidationError("Пользователь с таким email уже зарегистрирован")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"]
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
        return user


class ApplicationForm(forms.Form):
    organization_name = forms.CharField(
        label="Название бизнеса",
        min_length=2,
        max_length=200,
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "autocomplete": "organization"}),
    )
    inn = forms.RegexField(
        label="ИНН",
        regex=r"^(\d{10}|\d{12})$",
        error_messages={"invalid": "ИНН должен содержать 10 или 12 цифр"},
        widget=forms.TextInput(
            attrs={"class": INPUT_CLASS, "inputmode": "numeric", "autocomplete": "off"}
        ),
    )
    product = forms.ModelChoiceField(
        label="Кредитный продукт",
        queryset=Product.objects.none(),
        empty_label=None,
        widget=forms.RadioSelect,
    )
    amount = forms.DecimalField(
        label="Сумма, ₽",
        min_value=1,
        max_digits=14,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "step": "10000"}),
    )
    term_months = forms.IntegerField(
        label="Срок, месяцев",
        min_value=1,
        max_value=120,
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS}),
    )
    purpose = forms.CharField(
        label="На что нужны деньги?",
        min_length=10,
        max_length=1000,
        widget=forms.Textarea(attrs={"class": INPUT_CLASS.replace("h-12", "min-h-28 py-3"), "rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(active=True)

    def clean(self):
        data = super().clean()
        product = data.get("product")
        amount = data.get("amount")
        term = data.get("term_months")
        if product and amount is not None and not product.min_amount <= amount <= product.max_amount:
            self.add_error(
                "amount",
                f"Для этого продукта сумма от {product.min_amount:,.0f} до {product.max_amount:,.0f} ₽",
            )
        if product and term is not None and not product.min_term_months <= term <= product.max_term_months:
            self.add_error(
                "term_months",
                f"Для этого продукта срок от {product.min_term_months} до {product.max_term_months} месяцев",
            )
        return data


class ReviewActionForm(forms.Form):
    ACTION_TAKE = "take"
    ACTION_REQUEST_REVISION = "request_revision"
    ACTION_SEND_TO_APPROVAL = "send_to_approval"
    ACTION_APPROVE = "approve"
    ACTION_DECLINE = "decline"
    ACTION_RETURN_TO_REVIEW = "return_to_review"

    action = forms.ChoiceField(
        choices=(
            (ACTION_TAKE, "Взять в работу"),
            (ACTION_REQUEST_REVISION, "Вернуть на доработку"),
            (ACTION_SEND_TO_APPROVAL, "Передать на согласование"),
            (ACTION_APPROVE, "Одобрить"),
            (ACTION_DECLINE, "Отклонить"),
            (ACTION_RETURN_TO_REVIEW, "Вернуть сотруднику"),
        ),
        widget=forms.HiddenInput,
    )
    note = forms.CharField(
        label="Комментарий",
        required=False,
        max_length=2000,
        widget=forms.Textarea(
            attrs={"class": INPUT_CLASS.replace("h-12", "min-h-24 py-3"), "rows": 3}
        ),
    )

    def clean(self):
        data = super().clean()
        if data.get("action") in {
            self.ACTION_REQUEST_REVISION,
            self.ACTION_DECLINE,
            self.ACTION_RETURN_TO_REVIEW,
        } and len((data.get("note") or "").strip()) < 3:
            self.add_error("note", "Для этого действия укажите причину")
        return data


class CommentForm(forms.Form):
    text = forms.CharField(
        label="Комментарий",
        min_length=3,
        max_length=2000,
        widget=forms.Textarea(
            attrs={"class": INPUT_CLASS.replace("h-12", "min-h-24 py-3"), "rows": 3}
        ),
    )
    audience = forms.ChoiceField(
        label="Кому виден комментарий",
        choices=ApplicationComment.Audience.choices,
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )

