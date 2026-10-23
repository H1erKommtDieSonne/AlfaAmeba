from django import forms

from core.forms import INPUT_CLASS
from products.advisor import NEEDS


class NeedsForm(forms.Form):
    need = forms.ChoiceField(label="Что хотите сделать?", choices=[("", "Выберите задачу"), *NEEDS])
    gap_kind = forms.ChoiceField(label="Как устроен кассовый разрыв?", required=False, choices=[
        ("", "Выберите ситуацию"), ("short", "Разовый, есть ожидаемое поступление"),
        ("receivable", "Жду оплату уже выполненной поставки"), ("recurring", "Повторяется регулярно"),
        ("unknown", "Не знаю, за счёт чего погасить"),
    ])
    debt_kind = forms.ChoiceField(label="Какие долги?", required=False, choices=[
        ("", "Выберите обязательства"), ("loans", "Действующие кредиты без просрочки"),
        ("suppliers", "Долги поставщикам"), ("overdue", "Есть просрочка"),
    ])
    collateral = forms.BooleanField(label="Планирую предоставить залог", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field, forms.BooleanField):
                field.widget.attrs["class"] = INPUT_CLASS

    def clean(self):
        data = super().clean()
        for need, field in [("gap", "gap_kind"), ("debt", "debt_kind")]:
            if data.get("need") == need and not data.get(field):
                self.add_error(field, "Уточните ситуацию для подбора")
            elif data.get("need") != need:
                data[field] = ""
        return data
