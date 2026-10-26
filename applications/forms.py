from pathlib import Path

from django import forms

from core.forms import INPUT_CLASS, MultipleFileField, MultipleFileInput
from documents.models import ApplicationDocument
from products.models import Product
from products.advisor import DOCUMENTS, recommend, requirements
from .advisor_forms import NeedsForm

from .models import ApplicationComment


def _document_name(file):
    """Имя сохранённого ApplicationDocument (original_name) или свежей загрузки."""
    original_name = getattr(file, "original_name", "")
    if original_name:
        return original_name
    return getattr(file, "name", "")


class ApplicationForm(NeedsForm):
    # Контракт пакета совпадает с ограничениями узла документов: без .xls.
    ALLOWED_DOCUMENT_EXTENSIONS = {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".xlsx",
        ".csv",
        ".docx",
    }
    DOCUMENT_ACCEPT = ".pdf,.png,.jpg,.jpeg,.xlsx,.csv,.docx"
    MAX_DOCUMENT_SIZE = 10 * 1024 * 1024
    MAX_DOCUMENTS_SIZE = 50 * 1024 * 1024
    MAX_DOCUMENTS_COUNT = 5

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
        required=False,
        label="Дополнительные документы (необязательно, например годовая отчётность)",
        help_text=(
            "PDF, PNG, JPG, XLSX, CSV или DOCX — не более 5 файлов всего, "
            "до 10 МБ каждый и 50 МБ суммарно. Годовую отчётность можно "
            "приложить при наличии: отдельного обязательного требования нет."
        ),
        widget=MultipleFileInput(
            attrs={
                "class": "form-control file-control",
                "accept": DOCUMENT_ACCEPT,
            }
        ),
    )

    def __init__(self, *args, previous_version=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.previous_version = previous_version
        self.fields["product"].queryset = Product.objects.filter(active=True)
        self.fields["remove_document_ids"] = forms.ModelMultipleChoiceField(
            queryset=(
                previous_version.documents.all()
                if previous_version
                else ApplicationDocument.objects.none()
            ),
            required=False,
            label="Исключить документы из переноса",
            help_text=(
                "Отметьте документы предыдущей версии, которые не нужно "
                "переносить в новую версию. Файлы и история не удаляются."
            ),
        )
        for code, (name, reason) in DOCUMENTS.items():
            existing = previous_version.documents.filter(requirement_code=code).exists() if previous_version else False
            self.fields[f"document_{code}"] = MultipleFileField(
                label=name, required=False, help_text=reason,
                widget=MultipleFileInput(attrs={"class": "form-control", "accept": self.DOCUMENT_ACCEPT,
                                               "x-bind:required": f"requires('{code}') && {'false' if existing else 'true'}"}),
            )

    @property
    def document_fields(self):
        return [{"code": code, "field": self[f"document_{code}"],
                 "existing": list(self.previous_version.documents.filter(requirement_code=code)) if self.previous_version else []}
                for code in DOCUMENTS]

    @property
    def has_document_errors(self):
        return any(name == "documents" or name.startswith("document_") for name in self.errors)

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
        data["recommendation"] = recommend(data, self.fields["product"].queryset)
        if product and data.get("need") and not any(
            item["code"] == product.code and item["available"] for item in data["recommendation"]["items"]
        ):
            self.add_error("product", "Выберите доступный вариант из подбора или уточните задачу, сумму и срок.")
        checklist = requirements(product.code, data.get("collateral", False)) if product else []
        data["checklist"] = checklist
        required_codes = {item["code"] for item in checklist}

        # Принимаем только документы актуального комплекта: подделанный POST
        # со скрытыми полями прежнего продукта не должен попасть в версию.
        uploads = [("", file) for file in data.get("documents") or []]
        for code in DOCUMENTS:
            if code not in required_codes:
                continue
            fresh = data.get(f"document_{code}") or []
            uploads.extend((code, file) for file in fresh)

        # Явно загруженные файлы заменяют прежний документ того же пункта.
        # Прежние typed-документы переносим только для актуального комплекта,
        # чтобы после смены продукта не отправлять скрытые устаревшие файлы.
        fresh_codes = {code for code, _ in uploads if code}
        removed_ids = {
            document.pk for document in data.get("remove_document_ids", ())
        }
        retained = []
        if self.previous_version:
            previous_documents = self.previous_version.documents.exclude(pk__in=removed_ids)
            # Общие документы не теряются: новая загрузка дополняет прежние.
            retained.extend(previous_documents.filter(requirement_code=""))
            for code in required_codes:
                if code not in fresh_codes:
                    retained.extend(previous_documents.filter(requirement_code=code))

        for item in checklist:
            item["status"] = "uploaded" if (
                item["code"] in fresh_codes
                or any(doc.requirement_code == item["code"] for doc in retained)
            ) else "missing"
            if item["status"] == "missing":
                self.add_error(f"document_{item['code']}", "Прикрепите документ для выбранного продукта.")

        entries = list(uploads) + [(doc.requirement_code, doc) for doc in retained]
        if len(entries) > self.MAX_DOCUMENTS_COUNT:
            self.add_error(
                None,
                f"В одной версии можно передать не более {self.MAX_DOCUMENTS_COUNT} "
                "документов (с учётом уже приложенных).",
            )

        total_size = 0
        for code, file in entries:
            field = f"document_{code}" if code and code in DOCUMENTS else "documents"
            name = _document_name(file)
            suffix = Path(name).suffix.lower()
            if suffix not in self.ALLOWED_DOCUMENT_EXTENSIONS:
                if suffix == ".xls":
                    self.add_error(field, "Формат .xls не поддерживается; сохраните файл как .xlsx")
                else:
                    self.add_error(field, "Формат файла не поддерживается")
            if file.size <= 0:
                self.add_error(field, "Файл пуст")
            elif file.size > self.MAX_DOCUMENT_SIZE:
                self.add_error(field, "Размер файла превышает 10 МБ")
            total_size += file.size

        if total_size > self.MAX_DOCUMENTS_SIZE:
            self.add_error(None, "Общий размер документов превышает 50 МБ")
        data["document_uploads"] = uploads
        data["retained_documents"] = retained
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
