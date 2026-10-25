"""Финансовый результат внешнего узла (``financial_analysis``, схема v2) для карточки сотрудника.

Узел присылает результат в callback, и Django хранит его как есть
(``NodeJob.result``). Здесь он превращается в готовые для шаблона подписи, числа
и данные графиков. Модуль чистый: читает только известные поля с проверкой
типов, незнакомые поля пропускает, ничего не сохраняет и не меняет статусы.
Строгую проверку по JSON Schema здесь намеренно не делаем: узел развивает схему,
и новое поле не должно ронять карточку целиком. Схема — в
``docs/schemas/financial-result-v2.schema.json``, контракт — в
``docs/financial-api-v2.md``.

Всё, что здесь выводится, — расчёты узла по данным клиента, а не решение банка.
"""

import math

from .models import NodeJob

SCHEMA_VERSION = 2
PROCESSING_ERROR_STATUS = "processing_error"

TONE_POSITIVE = "positive"
TONE_WARNING = "warning"
TONE_NEGATIVE = "negative"
TONE_NEUTRAL = "neutral"

MONTHS_SHORT = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")

READINESS_LABELS = {
    "annual_model": "Годовая модель",
    "monthly_cash_flow": "Денежный поток по месяцам",
    "next_month_receipts_and_operating_payments": "Прогноз поступлений и платежей",
    "balance_after_all_debt": "Остаток после платежей по долгу",
    "dscr": "DSCR",
    "debt_to_ebitda": "Долг / EBITDA",
    "collateral_valuation": "Оценка залога",
}
# Для чего нужен запрос клиенту: те же анализы плюс проверка комплекта документов.
REQUIRED_FOR_LABELS = {**READINESS_LABELS, "product_document_check": "Проверка комплекта документов"}

ANNUAL_LABELS = {
    "revenue": "Выручка",
    "net_profit_current": "Чистая прибыль",
    "operating_profit_current": "Прибыль от продаж",
    "total_assets": "Активы",
    "equity": "Капитал",
    "current_assets": "Оборотные активы",
    "short_term_liabilities": "Краткосрочные обязательства",
    "cash": "Денежные средства",
    "receivables": "Дебиторская задолженность",
    "okved_group": "Группа ОКВЭД",
}

STATISTIC_LABELS = {
    "current_ratio": "Текущая ликвидность",
    "cash_ratio": "Денежная ликвидность",
    "equity_to_assets": "Капитал / активы",
    "net_margin": "Чистая маржа",
    "operating_margin": "Операционная маржа",
    "debt_to_ebitda": "Долг / EBITDA",
    "dscr": "DSCR",
    "collateral_value_rub": "Стоимость залога",
    "largest_customer_receipts_share": "Доля крупнейшего покупателя",
}
# Доли в схеме — 0.05, показываем как 5 %; остальные ratio — кратности вида 1,29.
PERCENT_STATISTICS = {"equity_to_assets", "net_margin", "operating_margin", "largest_customer_receipts_share"}
# Имена полей в формулах узла → человеческие слова; порядок — от длинных к коротким.
FORMULA_TERMS = (
    ("largest counterparty customer receipts", "поступления от крупнейшего покупателя"),
    ("all classified customer receipts", "все поступления от покупателей"),
    ("debt_service_same_period", "обслуживание долга за тот же период"),
    ("latest sourced valuation", "последняя подтверждённая оценка"),
    ("short_term_liabilities", "краткосрочные обязательства"),
    ("operating_profit_current", "прибыль от продаж"),
    ("net_profit_current", "чистая прибыль"),
    ("current_assets", "оборотные активы"),
    ("total_assets", "активы"),
    ("total_debt", "совокупный долг"),
    ("revenue", "выручка"),
    ("equity", "капитал"),
    ("ebitda", "EBITDA"),
    ("cfads", "CFADS"),
    ("cash", "денежные средства"),
)

SEVERITY_DISPLAY = {
    "high": ("Высокий", TONE_NEGATIVE),
    "medium": ("Средний", TONE_WARNING),
    "low": ("Низкий", TONE_POSITIVE),
}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

SCENARIO_LABELS = {"base": "Базовый", "cautious": "Осторожный", "stress": "Стрессовый"}

FILE_CATEGORY_LABELS = {
    "bank_statement": "Выписка по счёту",
    "contract": "Контракт",
    "estimate": "Смета",
    "credit_documents": "Кредитные документы",
    "ownership": "Право собственности",
    "financial_statements": "Бухгалтерская отчётность",
}

# Частые поля из document_fields; остальные показываем по имени без подчёркиваний.
DOCUMENT_FIELD_LABELS = {
    "account_number": "Номер счёта",
    "statement_period": "Период выписки",
    "contract_number": "Номер контракта",
    "contract_date": "Дата контракта",
    "contract_amount": "Сумма контракта",
    "cadastral_number": "Кадастровый номер",
    "collateral_value": "Стоимость залога",
    "ebitda": "EBITDA",
    "total_debt": "Совокупный долг",
    "cfads": "CFADS",
}

MODEL_OUTPUT_FIELDS = (
    ("decision", "Рекомендация модели", "text"),
    ("safe_credit_limit_rub_thousands", "Безопасный лимит", "thousands"),
    ("approval_probability_percent", "Вероятность одобрения", "percent"),
    ("default_probability_percent", "Вероятность дефолта", "percent"),
    ("loss_probability_percent", "Вероятность убытка", "percent"),
    ("liquidity_crisis_probability_percent", "Вероятность дефицита ликвидности", "percent"),
    ("forecast_revenue_rub_thousands", "Прогноз выручки", "thousands"),
    ("net_margin_percent", "Чистая маржа (прогноз)", "percent"),
    ("operating_margin_percent", "Операционная маржа (прогноз)", "percent"),
    ("operating_cash_flow_forecast_rub", "Операционный поток (прогноз)", "rub"),
)

# Строгий белый список статусов вывода модели. Всё прочее — «не запускалась».
MODEL_STATUS_COMPLETED = "completed"
MODEL_STATUS_FAILED = "failed"
MODEL_STATUS_NOT_RUN = "not_run"

# Обязательная оговорка: вывод модели — рекомендация, а не решение банка.
MODEL_NOT_BANK_DECISION_NOTE = "Не является решением банка."
# Безопасный технический текст для failed: сырое исключение/note узла наружу не идут.
MODEL_FAILED_MESSAGE = (
    "Расчёт ML-модели не выполнен из-за технической ошибки. "
    "Числовые прогнозы не рассчитаны. " + MODEL_NOT_BANK_DECISION_NOTE
)

# Только эти проценты — вероятности: обязаны лежать в 0..100. Маржи могут быть любыми.
PROBABILITY_MODEL_FIELDS = frozenset({
    "approval_probability_percent",
    "default_probability_percent",
    "loss_probability_percent",
    "liquidity_crisis_probability_percent",
})

CONCENTRATION_TOP = 5
# Подсказка интерфейса, а не норматив банка: с какой доли один покупатель заметен.
CONCENTRATION_NOTICE_SHARE = 0.5
MIN_FORECAST_MONTHS = 3


# --- безопасное чтение -----------------------------------------------------

def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _num(value):
    # bool — подкласс int, но числом в отчёте не бывает.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(value, maximum=2000):
    return value.strip()[:maximum] if isinstance(value, str) else ""


# --- форматирование ---------------------------------------------------------

def _decimal(value, digits):
    text = f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")
    return text[:-2] if digits == 1 and text.endswith(",0") else text


def money(rub):
    """23 100 000 → «23,1 млн ₽». ``None`` → «—»."""
    if rub is None:
        return "—"
    size = abs(rub)
    if size >= 1e9:
        return f"{_decimal(rub / 1e9, 1)} млрд ₽"
    if size >= 1e6:
        return f"{_decimal(rub / 1e6, 1)} млн ₽"
    if size >= 1e4:
        return f"{_decimal(rub / 1e3, 0)} тыс. ₽"
    return f"{_decimal(rub, 0)} ₽"


def percent(share):
    return "—" if share is None else f"{_decimal(share * 100, 1)} %"


def multiple(value):
    return "—" if value is None else _decimal(value, 2)


def date_label(value):
    """«2026-09-30» → «30.09.2026»; непонятную строку возвращаем как есть."""
    text = _text(value, 10)
    year, month, day = (text.split("-") + ["", ""])[:3]
    if len(year) == 4 and month.isdigit() and day.isdigit():
        return f"{day}.{month}.{year}"
    return text


def month_label(month):
    """«2026-07» → «июл 2026»; непонятную строку возвращаем как есть."""
    year, _, number = _text(month, 7).partition("-")
    if year.isdigit() and number.isdigit() and 1 <= int(number) <= 12:
        return f"{MONTHS_SHORT[int(number) - 1]} {year}"
    return _text(month, 20)


def formula_label(formula):
    text = _text(formula, 300)
    for term, label in FORMULA_TERMS:
        text = text.replace(term, label)
    return text


# --- блоки ------------------------------------------------------------------

def _source(raw):
    source = _dict(raw)
    origin = _text(source.get("filename"), 255) or _text(source.get("provider"), 120)
    return {
        "origin": origin or "источник не указан",
        "locator": _text(source.get("locator"), 200),
        "quote": _text(source.get("quote"), 1500),
        "is_mock": source.get("is_mock") is True,
    }


def _readiness(raw):
    readiness = _dict(raw)
    items = [
        {"key": key, "label": label, "ready": readiness.get(key) is True}
        for key, label in READINESS_LABELS.items()
    ]
    return {"items": items, "ready_count": sum(item["ready"] for item in items), "total": len(items)}


def _annual(raw):
    annual = _dict(raw)
    metrics = _dict(annual.get("metrics"))
    sources = _dict(annual.get("sources"))
    rows = []
    for key, label in ANNUAL_LABELS.items():
        value = metrics.get(key)
        if key == "okved_group":
            display = _text(value, 20) or "—"
        else:
            amount = _num(value)
            # Годовые входы приходят в тысячах рублей.
            display = money(amount * 1000) if amount is not None else "—"
        rows.append({
            "key": key,
            "label": label,
            "display": display,
            "missing": value is None,
            "sources": [_source(item) for item in _list(sources.get(key))],
        })
    period_end = _text(annual.get("period_end"), 10)
    return {
        "ready": annual.get("ready") is True,
        "year": period_end[:4],
        "missing": [ANNUAL_LABELS.get(key, key) for key in _list(annual.get("missing_fields")) if isinstance(key, str)],
        "rows": rows,
    }


def _months(raw_cash_flow):
    months = []
    for raw in _list(_dict(raw_cash_flow).get("months")):
        month = _dict(raw)
        if not _text(month.get("month")):
            continue
        breakdown = {
            _text(name, 120): amount
            for name, amount in _dict(month.get("customer_breakdown")).items()
            if _num(amount) is not None
        }
        months.append({
            "month": _text(month.get("month"), 7),
            "label": month_label(month.get("month")),
            "complete": month.get("complete") is True,
            "accounts": [_text(item, 120) for item in _list(month.get("accounts")) if isinstance(item, str)],
            "opening": _num(month.get("opening_balance")),
            "closing": _num(month.get("closing_balance")),
            "inflows": _num(month.get("inflows")),
            "outflows": _num(month.get("outflows")),
            "receipts": _num(month.get("customer_receipts")),
            "operating": _num(month.get("operating_payments")),
            "debt": _num(month.get("debt_service")),
            "breakdown": breakdown,
            "sources": [_source(item) for item in _list(month.get("sources"))],
        })
    return months


def _cash_flow(raw):
    cash_flow = _dict(raw)
    months = _months(cash_flow)
    for month in months:
        month["closing_display"] = money(month["closing"])
        month["receipts_display"] = money(month["receipts"])
        month["operating_display"] = money(month["operating"])
        month["debt_display"] = money(month["debt"])
        month["inflows_display"] = money(month["inflows"])
        month["outflows_display"] = money(month["outflows"])
    latest = _num(cash_flow.get("latest_observed_balance_rub"))
    return {
        "months": months,
        "complete_months": cash_flow.get("complete_months") if isinstance(cash_flow.get("complete_months"), int) else 0,
        "accounts_confirmed": cash_flow.get("accounts_confirmed") is True,
        "average_inflows": money(_num(cash_flow.get("average_monthly_inflows_rub"))),
        "average_outflows": money(_num(cash_flow.get("average_monthly_outflows_rub"))),
        "average_balance": money(_num(cash_flow.get("average_month_end_balance_rub"))),
        "latest_balance": latest,
        "latest_balance_display": money(latest),
        "latest_balance_date": date_label(cash_flow.get("latest_observed_balance_date")),
    }


def _statistics(raw):
    items = []
    for key, raw_item in _dict(raw).items():
        item = _dict(raw_item)
        if not item:
            continue
        value = _num(item.get("value"))
        available = item.get("status") == "available" and value is not None
        if not available:
            display = "нет данных"
        elif item.get("unit") == "RUB":
            display = money(value)
        elif key in PERCENT_STATISTICS:
            display = percent(value)
        else:
            display = multiple(value)
        items.append({
            "key": key,
            "label": STATISTIC_LABELS.get(key, key),
            "display": display,
            "value": value,
            "available": available,
            "formula": formula_label(item.get("formula")),
            "reason": _text(item.get("reason"), 300),
        })
    # Сначала посчитанные: недоступные — справочно, в конце.
    items.sort(key=lambda item: not item["available"])
    return items


def _scenario(raw):
    scenario = _dict(raw)
    name = _text(scenario.get("name"), 20)
    assumptions = _dict(scenario.get("assumptions"))
    receipts_factor = _num(assumptions.get("receipts_factor"))
    payments_factor = _num(assumptions.get("operating_payments_factor"))
    gap = _num(scenario.get("cash_gap_rub"))
    path = []
    for raw_step in _list(scenario.get("projection_path")):
        step = _dict(raw_step)
        path.append({
            "month": _text(step.get("month"), 7),
            "label": month_label(step.get("month")),
            "closing": _num(step.get("closing_balance_rub")),
        })
    return {
        "name": name,
        "label": SCENARIO_LABELS.get(name, name),
        "assumptions": (
            f"поступления ×{multiple(receipts_factor)} · расходы ×{multiple(payments_factor)}"
            if receipts_factor is not None and payments_factor is not None else ""
        ),
        "receipts": money(_num(scenario.get("customer_receipts_rub"))),
        "operating": money(_num(scenario.get("operating_payments_rub"))),
        "existing_debt": money(_num(scenario.get("existing_debt_service_rub"))),
        "new_payment": money(_num(scenario.get("proposed_credit_payment_rub"))),
        "balance_after_all_debt": money(_num(scenario.get("balance_after_all_debt_rub"))),
        "cash_gap": gap,
        "cash_gap_display": money(gap),
        "has_gap": gap is not None and gap > 0,
        "path": path,
    }


def _forecast(raw):
    forecast = _dict(raw)
    scenarios = [_scenario(item) for item in _list(forecast.get("scenarios"))]
    backtest = _dict(forecast.get("backtest"))
    folds = backtest.get("folds") if isinstance(backtest.get("folds"), int) else 0
    return {
        "ready": forecast.get("ready") is True and bool(scenarios),
        "period": month_label(forecast.get("period")) if forecast.get("period") else "",
        "data_as_of": date_label(forecast.get("data_as_of")),
        "scenarios": scenarios,
        "stress": next((item for item in scenarios if item["name"] == "stress"), None),
        "limitations": [_text(item, 500) for item in _list(forecast.get("limitations")) if isinstance(item, str)],
        "backtest": {
            "folds": folds,
            "mae": money(_num(backtest.get("mae_rub"))),
            "mape": "—" if _num(backtest.get("mape_percent")) is None else f"{_decimal(_num(backtest.get('mape_percent')), 1)} %",
        },
    }


def _concentration(months):
    totals = {}
    for month in months:
        for name, amount in month["breakdown"].items():
            totals[name] = totals.get(name, 0.0) + float(amount)
    total = sum(totals.values())
    if total <= 0:
        return []
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    top, rest = ranked[:CONCENTRATION_TOP], ranked[CONCENTRATION_TOP:]
    if rest:
        top.append(("Прочие", sum(amount for _, amount in rest)))
    return [
        {"name": name, "amount": amount, "amount_display": money(amount), "share": amount / total, "share_display": percent(amount / total)}
        for name, amount in top
    ]


def _warnings(raw):
    items = []
    for raw_item in _list(raw):
        item = _dict(raw_item)
        message = _text(item.get("message"), 1000)
        if not message:
            continue
        severity = item.get("severity") if item.get("severity") in SEVERITY_DISPLAY else ""
        label, tone = SEVERITY_DISPLAY.get(severity, ("", TONE_NEUTRAL))
        items.append({
            "code": _text(item.get("code"), 64),
            "message": message,
            "severity": severity,
            "severity_label": label,
            "tone": tone,
        })
    items.sort(key=lambda item: SEVERITY_ORDER.get(item["severity"], len(SEVERITY_ORDER)))
    return items


def _client_requests(raw):
    items = []
    for raw_item in _list(raw):
        item = _dict(raw_item)
        title = _text(item.get("title"), 200)
        message = _text(item.get("message"), 1000)
        if not (title or message):
            continue
        required = item.get("priority") == "required"
        items.append({
            "code": _text(item.get("code"), 64),
            "title": title or message,
            "message": message,
            "required": required,
            "priority_label": "Обязательно" if required else "Дополнительно",
            "action_label": "Проверить или заменить файл" if item.get("action") == "review_or_replace" else "Загрузить или уточнить",
            "required_for": [
                REQUIRED_FOR_LABELS.get(key, key) for key in _list(item.get("required_for")) if isinstance(key, str)
            ],
            "filename": _text(item.get("filename"), 255),
        })
    # Обязательное — первым; внутри групп порядок узла.
    items.sort(key=lambda item: not item["required"])
    return items


def feedback_draft(client_requests):
    """Черновик комментария клиенту из запросов узла; сотрудник правит его перед отправкой."""
    required = [item for item in client_requests if item["required"]]
    optional = [item for item in client_requests if not item["required"]]
    if not (required or optional):
        return ""
    lines = []
    if required:
        lines.append("Чтобы продолжить рассмотрение заявки, пожалуйста:")
        lines += [f"{number}. {item['title']}. {item['message']}" for number, item in enumerate(required, 1)]
    if optional:
        if lines:
            lines.append("")
        lines.append("Дополнительно, если есть возможность:")
        lines += [f"— {item['title']}. {item['message']}" for item in optional]
    return "\n".join(lines)


def _model_number(value):
    """Конечное число без ``bool``/строк/NaN/inf; иначе ``None``.

    Проверка локальная: глобальный ``_num`` намеренно не меняем, чтобы не
    затрагивать остальные финансовые блоки.
    """
    number = _num(value)
    if number is None or not math.isfinite(number):
        return None
    return number


def _model_entry(key, label, kind, value):
    """Готовая пара «подпись → значение» или ``None``, если показывать нечего.

    Для ``completed`` нули — легитимные значения; вероятности вне 0..100,
    отрицательные суммы и нечисловые/неконечные значения не показываем.
    """
    if kind == "text":
        text = _text(value, 120)
        return {"label": label, "display": text} if text else None
    number = _model_number(value)
    if number is None:
        return None
    if key in PROBABILITY_MODEL_FIELDS and not 0.0 <= number <= 100.0:
        return None
    if kind in ("thousands", "rub") and number < 0:
        return None
    if kind == "thousands":
        display = money(number * 1000)
    elif kind == "percent":
        display = f"{_decimal(number, 1)}\u00a0%"
    else:
        display = money(number)
    return {"label": label, "display": display}


def _model_outputs(raw):
    """Строгий белый список статусов вывода модели.

    ``completed`` — единственный статус, при котором показываем конечные
    корректные числа. ``failed`` — отдельное сообщение без числовых полей.
    ``not_run``/отсутствующий/неизвестный статус — «ещё не запускалась».
    """
    outputs = _dict(raw)
    status = outputs.get("status")
    if status == MODEL_STATUS_COMPLETED:
        entries = [
            entry for key, label, kind in MODEL_OUTPUT_FIELDS
            if (entry := _model_entry(key, label, kind, outputs.get(key))) is not None
        ]
        return {
            "run": True,
            "status": MODEL_STATUS_COMPLETED,
            "failed": False,
            "risk_grade": _text(outputs.get("risk_grade"), 10),
            "entries": entries,
            "note": _text(outputs.get("note"), 1000),
            "disclaimer": MODEL_NOT_BANK_DECISION_NOTE,
        }
    if status == MODEL_STATUS_FAILED:
        return {
            "run": False,
            "status": MODEL_STATUS_FAILED,
            "failed": True,
            "risk_grade": "",
            "entries": [],
            "note": MODEL_FAILED_MESSAGE,
            "disclaimer": MODEL_NOT_BANK_DECISION_NOTE,
        }
    return {
        "run": False,
        "status": MODEL_STATUS_NOT_RUN,
        "failed": False,
        "risk_grade": "",
        "entries": [],
        "note": "",
        "disclaimer": "",
    }


def _document_fields(raw):
    fields = []
    for raw_item in _list(raw):
        item = _dict(raw_item)
        name = _text(item.get("name"), 64)
        if not name:
            continue
        value = item.get("value")
        unit = item.get("unit")
        if unit == "RUB" and _num(value) is not None:
            display = money(_num(value))
        elif unit == "RUB_THOUSANDS" and _num(value) is not None:
            display = money(_num(value) * 1000)
        elif unit == "DATE":
            display = date_label(value) or "—"
        elif _num(value) is not None:
            display = _decimal(_num(value), 2).removesuffix(",00")
        else:
            display = _text(value, 300) or "—"
        fields.append({
            "name": DOCUMENT_FIELD_LABELS.get(name, name.replace("_", " ").capitalize()),
            "display": display,
            "source": _source(item.get("source")),
        })
    return fields


def issue_tone(code):
    """Нечитаемое — серьёзнее, чем недостающее: первое мешает проверить уже присланное."""
    if code.startswith("unreadable") or code.endswith("_unreadable"):
        return TONE_NEGATIVE
    if code.startswith("missing"):
        return TONE_WARNING
    return TONE_NEUTRAL


def _issues(raw):
    issues = []
    for raw_item in _list(raw):
        item = _dict(raw_item)
        message = _text(item.get("message"), 1000)
        if not message:
            continue
        code = _text(item.get("code"), 64)
        issues.append({
            "code": code,
            "tone": issue_tone(code),
            "message": message,
            "suggestion": _text(item.get("suggestion"), 1000),
            "field": _text(item.get("field"), 120),
            "filename": _text(item.get("filename"), 255),
            "locator": _text(item.get("locator"), 200),
        })
    return issues


def _files(raw):
    files = []
    for raw_item in _list(raw):
        item = _dict(raw_item)
        name = _text(item.get("filename"), 255)
        if not name:
            continue
        categories = [FILE_CATEGORY_LABELS.get(key, key) for key in _list(item.get("categories")) if isinstance(key, str)]
        uncertain = len(_list(item.get("uncertain_fragments")))
        problems = item.get("truncated") is True or uncertain > 0
        files.append({
            "filename": name,
            "categories": categories,
            "checked": item.get("status") == "checked",
            "problems": problems,
            "note": (
                "проверен не полностью" if item.get("truncated") is True
                else f"сомнительных фрагментов: {uncertain}" if uncertain else ""
            ),
        })
    return files


def _highlights(cash_flow, annual, forecast, concentration):
    """Короткие выводы из данных узла — факты с тоном, без собственных оценок банка."""
    items = []
    months = cash_flow["complete_months"]
    if months >= MIN_FORECAST_MONTHS and cash_flow["accounts_confirmed"]:
        items.append((TONE_POSITIVE, f"Выписка покрывает {months} полных мес. подряд по подтверждённым счетам."))
    else:
        items.append((TONE_WARNING, f"Полных месяцев выписки: {months}. Для прогноза нужно минимум {MIN_FORECAST_MONTHS} по всем подтверждённым счетам."))
    if annual["ready"]:
        items.append((TONE_POSITIVE, f"Годовые показатели за {annual['year'] or 'отчётный год'} собраны для модели."))
    elif annual["missing"]:
        items.append((TONE_WARNING, "Для годовой модели не хватает: " + ", ".join(annual["missing"]) + "."))
    stress = forecast["stress"]
    if forecast["ready"] and stress:
        if stress["has_gap"]:
            items.append((TONE_NEGATIVE, f"В стрессовом сценарии на {forecast['period']} — разрыв {stress['cash_gap_display']}."))
        else:
            items.append((TONE_POSITIVE, f"Даже в стрессовом сценарии на {forecast['period']} остаток после всех платежей — {stress['balance_after_all_debt']}."))
    if concentration and concentration[0]["name"] != "Прочие" and concentration[0]["share"] >= CONCENTRATION_NOTICE_SHARE:
        items.append((TONE_WARNING, f"Крупнейший покупатель даёт {concentration[0]['share_display']} поступлений."))
    return [{"tone": tone, "message": message} for tone, message in items]


def _chart_data(cash_flow, forecast, concentration):
    """Только числа и подписи для Chart.js; выводится в шаблон через json_script."""
    months = cash_flow["months"]
    data = {"cashflow": None, "forecast": None, "concentration": None}
    if months:
        data["cashflow"] = {
            "labels": [month["label"] for month in months],
            "complete": [month["complete"] for month in months],
            "receipts": [month["receipts"] for month in months],
            "operating": [month["operating"] for month in months],
            "debt": [month["debt"] for month in months],
            "closing": [month["closing"] for month in months],
        }
    if forecast["ready"]:
        first_path = forecast["scenarios"][0]["path"]
        start_label = months[-1]["label"] if months else "факт"
        data["forecast"] = {
            "labels": [start_label] + [step["label"] for step in first_path],
            "scenarios": [
                {
                    "name": scenario["name"],
                    "label": scenario["label"],
                    "values": [cash_flow["latest_balance"]] + [step["closing"] for step in scenario["path"]],
                }
                for scenario in forecast["scenarios"]
            ],
        }
    if concentration:
        data["concentration"] = {
            "labels": [item["name"] for item in concentration],
            "values": [item["amount"] for item in concentration],
        }
    return data


def financial_view(node_job):
    """Представление финансового результата узла или ``None``.

    ``None`` — задания нет или callback ещё не пришёл. ``state``:
    ``processing_error`` — техническая ошибка узла (финансов нет по контракту),
    ``unsupported`` — результат старой версии или не похож на v2,
    ``ready`` — можно показывать.
    """
    if node_job is None or getattr(node_job, "status", None) != NodeJob.Status.COMPLETED:
        return None
    payload = _dict(getattr(node_job, "result", None))
    inner = _dict(payload.get("result"))
    if inner.get("status") == PROCESSING_ERROR_STATUS:
        return {"state": "processing_error"}
    analysis = _dict(inner.get("financial_analysis"))
    if analysis.get("schema_version") != SCHEMA_VERSION:
        return {"state": "unsupported"}

    cash_flow = _cash_flow(analysis.get("cash_flow"))
    annual = _annual(analysis.get("annual_model_input"))
    forecast = _forecast(analysis.get("next_month_forecast"))
    concentration = _concentration(cash_flow["months"])
    client_requests = _client_requests(analysis.get("client_requests"))
    processing = _dict(inner.get("processing"))
    return {
        "state": "ready",
        "is_mock": analysis.get("is_mock") is True,
        "summary": _text(inner.get("summary"), 1000),
        "analysis_date": date_label(_dict(analysis.get("application")).get("analysis_date")),
        "model": _text(payload.get("model"), 120),
        "readiness": _readiness(analysis.get("readiness")),
        "annual": annual,
        "cash_flow": cash_flow,
        "statistics": _statistics(analysis.get("statistics")),
        "forecast": forecast,
        "concentration": concentration,
        "warnings": _warnings(analysis.get("warnings")),
        "client_requests": client_requests,
        "required_requests": sum(item["required"] for item in client_requests),
        "feedback_draft": feedback_draft(client_requests),
        "employee_recommendations": [
            _text(_dict(item).get("message"), 1000)
            for item in _list(analysis.get("employee_recommendations"))
            if _text(_dict(item).get("message"))
        ],
        "model_outputs": _model_outputs(analysis.get("model_outputs")),
        "document_fields": _document_fields(analysis.get("document_fields")),
        "issues": _issues(inner.get("issues")),
        "files": _files(payload.get("files")),
        "highlights": _highlights(cash_flow, annual, forecast, concentration),
        "processing": {
            "files_received": processing.get("files_received") if isinstance(processing.get("files_received"), int) else None,
            "accepted_observations": processing.get("accepted_observations") if isinstance(processing.get("accepted_observations"), int) else None,
            "documents_checked": inner.get("documents_checked") if isinstance(inner.get("documents_checked"), int) else None,
        },
        "chart_data": _chart_data(cash_flow, forecast, concentration),
    }
