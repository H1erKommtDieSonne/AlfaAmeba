from pathlib import Path

from django import forms

from core.forms import INPUT_CLASS, MultipleFileField, MultipleFileInput
from products.models import Product

from .models import ApplicationComment


class ApplicationForm(forms.Form):
    ALLOWED_DOCUMENT_EXTENSIONS = {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".xlsx",
        ".xls",
        ".csv",
        ".docx",
    }
    MAX_DOCUMENT_SIZE = 20 * 1024 * 1024
    MAX_DOCUMENTS_SIZE = 50 * 1024 * 1024

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
        widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "step": "any"}),
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
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3}),
    )
    documents = MultipleFileField(
        label="Документы",
        help_text="PDF, изображения, таблицы или DOCX — до 20 МБ каждый и 50 МБ суммарно",
        widget=MultipleFileInput(
            attrs={
                "class": "form-control file-control",
                "accept": ".pdf,.png,.jpg,.jpeg,.xlsx,.xls,.csv,.docx",
            }
        ),
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

    def clean_documents(self):
        documents = self.cleaned_data["documents"]
        total_size = 0
        for document in documents:
            suffix = Path(document.name).suffix.lower()
            if suffix not in self.ALLOWED_DOCUMENT_EXTENSIONS:
                raise forms.ValidationError(f"Формат {suffix or 'без расширения'} не поддерживается")
            if document.size > self.MAX_DOCUMENT_SIZE:
                raise forms.ValidationError(f"Файл {document.name} превышает 20 МБ")
            total_size += document.size
        if total_size > self.MAX_DOCUMENTS_SIZE:
            raise forms.ValidationError("Общий размер документов превышает 50 МБ")
        return documents


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
            attrs={"class": INPUT_CLASS, "rows": 3}
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
            attrs={"class": INPUT_CLASS, "rows": 3}
        ),
    )
    audience = forms.ChoiceField(
        label="Кому виден комментарий",
        choices=ApplicationComment.Audience.choices,
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
