"""Форма данных для расчёта узла, которые подтверждает сотрудник банка."""

import re
from decimal import Decimal

from django import forms
from django.utils import timezone

from core.forms import INPUT_CLASS

from .models import FinancialInputs

MAX_ACCOUNTS = 20
MAX_STATEMENTS = 5
ACCOUNT_PATTERN = re.compile(r"^[\w.\-/]{1,64}$")


def annuity_payment(amount, term_months, annual_rate_percent):
    """Аннуитетный платёж; при нулевой ставке — равные доли основного долга."""
    amount = Decimal(amount)
    rate = Decimal(annual_rate_percent) / Decimal(1200)
    if rate == 0:
        return (amount / term_months).quantize(Decimal("0.01"))
    payment = amount * rate / (1 - (1 + rate) ** -term_months)
    return payment.quantize(Decimal("0.01"))


class FinancialInputsForm(forms.ModelForm):
    """Подтверждения сотрудника. Счета вводятся строками; платёж по умолчанию — аннуитет."""

    accounts = forms.CharField(
        label="Все расчётные счета клиента",
        required=False,
        help_text="По одному в строке. Без полного списка узел не считает денежный поток полным.",
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3}),
    )

    class Meta:
        model = FinancialInputs
        fields = ("annual_rate_percent", "proposed_monthly_payment_rub", "debt_status",
                  "debt_schedule_start", "debt_schedule_end", "reporting_year")
        widgets = {
            "annual_rate_percent": forms.NumberInput(attrs={"class": INPUT_CLASS, "step": "0.01", "min": "0"}),
            "proposed_monthly_payment_rub": forms.NumberInput(attrs={"class": INPUT_CLASS, "step": "0.01", "min": "0"}),
            "debt_status": forms.Select(attrs={"class": INPUT_CLASS}),
            "debt_schedule_start": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}, format="%Y-%m-%d"),
            "debt_schedule_end": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}, format="%Y-%m-%d"),
            "reporting_year": forms.NumberInput(attrs={"class": INPUT_CLASS}),
        }
        help_texts = {
            "proposed_monthly_payment_rub": "Пусто — посчитаем аннуитет по ставке, сумме и сроку заявки.",
            "reporting_year": "Пусто — последний полный год.",
        }

    def __init__(self, *args, version, **kwargs):
        super().__init__(*args, **kwargs)
        self.version = version
        if self.instance.pk and not self.is_bound:
            self.fields["accounts"].initial = "\n".join(self.instance.expected_account_ids or [])

    def clean_annual_rate_percent(self):
        value = self.cleaned_data.get("annual_rate_percent")
        if value is not None and not Decimal("0") <= value <= Decimal("100"):
            raise forms.ValidationError("Ставка — от 0 до 100 % годовых")
        return value

    def clean_reporting_year(self):
        value = self.cleaned_data.get("reporting_year")
        if value is not None and not 2000 <= value <= timezone.localdate().year:
            raise forms.ValidationError("Укажите год от 2000 до текущего")
        return value

    def clean_accounts(self):
        accounts = []
        for line in self.cleaned_data.get("accounts", "").splitlines():
            account = line.strip()
            if not account:
                continue
            if not ACCOUNT_PATTERN.fullmatch(account):
                raise forms.ValidationError(f"Номер счёта «{account[:40]}» — только буквы, цифры и . - /, до 64 символов")
            if account not in accounts:
                accounts.append(account)
        if len(accounts) > MAX_ACCOUNTS:
            raise forms.ValidationError(f"Не больше {MAX_ACCOUNTS} счетов")
        return accounts

    def clean(self):
        data = super().clean()
        if data.get("debt_status") == FinancialInputs.DebtStatus.COMPLETE:
            start, end = data.get("debt_schedule_start"), data.get("debt_schedule_end")
            if not (start and end):
                self.add_error("debt_schedule_end", "Укажите, за какой период приложены графики всех долгов")
            elif end < start:
                self.add_error("debt_schedule_end", "Конец периода раньше начала")
        else:
            data["debt_schedule_start"] = data["debt_schedule_end"] = None
        rate = data.get("annual_rate_percent")
        if data.get("proposed_monthly_payment_rub") is None and rate is not None:
            data["proposed_monthly_payment_rub"] = annuity_payment(self.version.amount, self.version.term_months, rate)
        return data

    def save(self, *, statements, user, commit=True):
        inputs = super().save(commit=False)
        inputs.version = self.version
        inputs.expected_account_ids = self.cleaned_data["accounts"]
        inputs.statement_periods = statements
        inputs.confirmed_by = user
        inputs.confirmed_at = timezone.now()
        if commit:
            inputs.save()
        return inputs


class StatementPeriodForm(forms.Form):
    """Период и остатки одной выписки: по ним узел сверяет, что месяц полный."""

    filename = forms.ChoiceField(label="Файл выписки", required=False)
    account_id = forms.CharField(label="Счёт", required=False, max_length=64,
                                 widget=forms.TextInput(attrs={"class": INPUT_CLASS}))
    period_start = forms.DateField(label="С", required=False,
                                   widget=forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}, format="%Y-%m-%d"))
    period_end = forms.DateField(label="По", required=False,
                                 widget=forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}, format="%Y-%m-%d"))
    opening_balance_rub = forms.DecimalField(label="Остаток на начало, ₽", required=False, max_digits=16, decimal_places=2,
                                             widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "step": "0.01"}))
    closing_balance_rub = forms.DecimalField(label="Остаток на конец, ₽", required=False, max_digits=16, decimal_places=2,
                                             widget=forms.NumberInput(attrs={"class": INPUT_CLASS, "step": "0.01"}))
    complete = forms.BooleanField(label="Выписка полная за весь период", required=False)

    def __init__(self, *args, filenames=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["filename"].choices = [("", "— не выбрано —"), *[(name, name) for name in filenames]]
        self.fields["filename"].widget.attrs["class"] = INPUT_CLASS

    def clean(self):
        data = super().clean()
        if not data.get("filename"):
            return data
        for name in ("account_id", "period_start", "period_end", "opening_balance_rub", "closing_balance_rub"):
            if data.get(name) in (None, ""):
                self.add_error(name, "Заполните для выбранной выписки")
        start, end = data.get("period_start"), data.get("period_end")
        if start and end and end < start:
            self.add_error("period_end", "Конец периода раньше начала")
        return data

    def as_period(self):
        """Период в виде, который хранится в FinancialInputs и уходит узлу."""
        data = self.cleaned_data
        return {
            "filename": data["filename"],
            "account_id": data["account_id"].strip(),
            "period_start": data["period_start"].isoformat(),
            "period_end": data["period_end"].isoformat(),
            "opening_balance_rub": float(data["opening_balance_rub"]),
            "closing_balance_rub": float(data["closing_balance_rub"]),
            "complete": data["complete"],
        }


class BaseStatementFormSet(forms.BaseFormSet):
    def __init__(self, *args, filenames=(), accounts=(), **kwargs):
        self.filenames = list(filenames)
        self.accounts = list(accounts)
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        return {**super().get_form_kwargs(index), "filenames": self.filenames}

    def clean(self):
        if any(self.errors):
            return
        seen = set()
        for form in self.forms:
            if not form.cleaned_data.get("filename"):
                continue
            key = (form.cleaned_data["filename"], form.cleaned_data["account_id"].strip())
            if key in seen:
                raise forms.ValidationError("Одна и та же выписка по одному счёту указана дважды")
            seen.add(key)
            # Узел сверяет выписки со списком всех счетов клиента: счёт выписки обязан в нём быть.
            if self.accounts and key[1] not in self.accounts:
                form.add_error("account_id", "Этого счёта нет в списке расчётных счетов клиента")

    def periods(self):
        return [form.as_period() for form in self.forms if form.cleaned_data.get("filename")]


StatementFormSet = forms.formset_factory(
    StatementPeriodForm, formset=BaseStatementFormSet, extra=0, max_num=MAX_STATEMENTS, validate_max=True
)


def statement_formset(data=None, *, version, inputs=None, accounts=(), suggestions=None):
    """Формы периодов: сохранённые строки плюс строка на каждую ещё не описанную выписку.

    Выбрать можно любой файл версии, а заранее подставляются только те, что клиент
    приложил в пункт «выписка», — иначе пришлось бы стирать строку отчётности.
    """
    documents = list(version.documents.order_by("created_at").values_list("original_name", "requirement_code"))
    filenames = list(dict.fromkeys(name for name, _ in documents))
    initial = []
    if inputs:
        for period in inputs.statement_periods or []:
            if isinstance(period, dict):
                initial.append({**period, "complete": period.get("complete") is True})
    elif suggestions:
        # Suggestions are drafts only: filename/account/period may be prefilled,
        # balances stay empty and complete can be set only by the employee.
        for row in suggestions.get("rows") or []:
            if isinstance(row, dict) and row.get("filename"):
                initial.append({**row, "complete": False})
    described = {row.get("filename") for row in initial}
    statements = list(dict.fromkeys(name for name, code in documents if code == "statement" and name not in described))
    initial += [{"filename": name} for name in statements][: max(0, MAX_STATEMENTS - len(initial))]
    return StatementFormSet(data, prefix="statements", initial=initial or [{}], filenames=filenames, accounts=accounts)
