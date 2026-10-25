"""Безопасное представление вложенного результата внешнего узла для интерфейса.

Узел возвращает внешний статус ``completed`` и вложенный ``result.status``.
Здесь вложенный статус превращается в подпись, тон и объяснение «не решение
банка». Модуль чистый: он только читает ``node_job.result['result']['status']``
по белому списку и никогда не выводит сырой ``summary``, текст исключения или
``issues``. Замечания к документам приходят отдельным feedback и здесь не
извлекаются, чтобы не дублировать их.

``NodeJob.status == COMPLETED`` означает только доставленный callback. Помощник
никогда не меняет статус задания и не превращает ``processing_error`` в
``FAILED`` или повторную отправку.
"""

from .models import NodeJob

TONE_POSITIVE = "status--positive"
TONE_WARNING = "status--warning"
TONE_NEUTRAL = "status--neutral"
TONE_NEGATIVE = "status--negative"

PROCESSING_ERROR_STATUS = "processing_error"
# Клиентобезопасный текст: не раскрывает исключение, сводку модели или детали.
PROCESSING_ERROR_MESSAGE = (
    "Техническая ошибка проверки. Это не замечание к вашим документам; "
    "требуется проверка сотрудником."
)

# Белый список: статус → фиксированные подпись, тон и объяснение без сырых данных.
_NODE_REVIEW_DISPLAY = {
    "complete": {
        "label": "Проверка комплектности завершена",
        "tone": TONE_POSITIVE,
        "message": (
            "Внешний узел завершил проверку комплектности документов. "
            "Это не подтверждение достоверности и не решение банка."
        ),
    },
    "needs_documents": {
        "label": "Нужны документы",
        "tone": TONE_WARNING,
        "message": (
            "Внешний узел сообщил, что комплект документов неполный. "
            "Требуется уточнение состава; это не решение банка."
        ),
    },
    "needs_review": {
        "label": "Требуется проверка сотрудником",
        "tone": TONE_WARNING,
        "message": (
            "Внешний узел нашёл неопределённости или противоречия. "
            "Требуется проверка сотрудником; это не решение банка."
        ),
    },
    PROCESSING_ERROR_STATUS: {
        "label": "Техническая ошибка проверки",
        "tone": TONE_NEGATIVE,
        "message": PROCESSING_ERROR_MESSAGE,
    },
}


def node_review(node_job):
    """Вернуть ``{status,label,tone,message}`` или ``None``.

    ``None`` для отсутствующего/незавершённого задания (статус не
    ``COMPLETED``), старого callback без вложенного ``result`` и любого
    неизвестного или некорректного статуса. Статус ``NodeJob`` не изменяется.
    """
    if node_job is None:
        return None
    if getattr(node_job, "status", None) != NodeJob.Status.COMPLETED:
        return None
    payload = getattr(node_job, "result", None)
    if not isinstance(payload, dict):
        return None
    inner = payload.get("result")
    if not isinstance(inner, dict):
        return None
    status = inner.get("status")
    if not isinstance(status, str):
        return None
    display = _NODE_REVIEW_DISPLAY.get(status)
    if display is None:
        return None
    return {
        "status": status,
        "label": display["label"],
        "tone": display["tone"],
        "message": display["message"],
    }


# --- Банковская сводка финансового анализа (schema v2) ---------------------

FINANCIAL_SCHEMA_VERSION = 2
MAX_FINANCIAL_WARNINGS = 20
MAX_WARNING_FIELDS = 10
MAX_FIELD_CHARS = 120
MAX_CODE_CHARS = 64
# Единая формулировка: готовность данных — не запуск модели и не решение банка.
MODEL_STATUS_MESSAGE = (
    "Готовность данных не означает запуск модели или решение банка."
)
# Точные ключи readiness узла (7): фиксированные безопасные подписи.
READINESS_LABELS = (
    ("annual_model", "Годовые входы модели"),
    ("monthly_cash_flow", "Месячный денежный поток"),
    (
        "next_month_receipts_and_operating_payments",
        "Прогноз поступлений и операционных платежей",
    ),
    ("balance_after_all_debt", "Остаток после всех долгов"),
    ("dscr", "DSCR"),
    ("debt_to_ebitda", "Долг/EBITDA"),
    ("collateral_valuation", "Оценка обеспечения"),
)
# Белый список важности замечаний; не оценка кредитного риска.
SEVERITY_LABELS = {
    "low": "Низкая важность",
    "medium": "Средняя важность",
    "high": "Высокая важность",
}


def _strict_bool(value):
    """Только строгий ``bool``; всё остальное (в т.ч. 0/1) — ``None``."""
    return value if isinstance(value, bool) else None


def _strict_int(value):
    """Только ``int`` без ``bool``; иначе ``None`` (не подставляем 0/false)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _bounded_str(value, maximum):
    """Непустая обрезанная строка или ``None``. HTML экранирует шаблон."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:maximum]


def _warning_projection(item):
    """Одно замечание по белому списку или ``None`` для мусора/чужой важности."""
    if not isinstance(item, dict):
        return None
    code = _bounded_str(item.get("code"), MAX_CODE_CHARS)
    if code is None:
        return None
    severity = item.get("severity")
    if not isinstance(severity, str) or severity not in SEVERITY_LABELS:
        return None
    raw_fields = item.get("fields")
    fields = []
    if isinstance(raw_fields, list):
        for value in raw_fields[:MAX_WARNING_FIELDS]:
            text = _bounded_str(value, MAX_FIELD_CHARS)
            if text is not None:
                fields.append(text)
    return {
        "code": code,
        "severity": severity,
        "label": SEVERITY_LABELS[severity],
        "fields": fields,
    }


def node_financial_summary(node_job):
    """Безопасная банковская сводка ``financial_analysis`` (schema v2) или ``None``.

    Только чтение: возвращает ограниченный словарь без числовых показателей,
    сырых сводок/исключений и решений ``model_outputs``. ``None`` для
    незавершённого задания, старого callback, не-``int``/чужой версии схемы и
    отсутствующего финансового блока. Неизвестные readiness/флаги остаются
    ``None``, а не ``false``; ``is_mock`` сохраняет различие ``None``/``False``.
    """
    if node_job is None:
        return None
    if getattr(node_job, "status", None) != NodeJob.Status.COMPLETED:
        return None
    payload = getattr(node_job, "result", None)
    if not isinstance(payload, dict):
        return None
    inner = payload.get("result")
    if not isinstance(inner, dict):
        return None
    # Строго int 2: True == 1, "2" и 2.0 не подходят.
    if _strict_int(inner.get("schema_version")) != FINANCIAL_SCHEMA_VERSION:
        return None
    financial = inner.get("financial_analysis")
    if not isinstance(financial, dict):
        return None
    if _strict_int(financial.get("schema_version")) != FINANCIAL_SCHEMA_VERSION:
        return None

    readiness_source = financial.get("readiness")
    readiness_source = readiness_source if isinstance(readiness_source, dict) else {}
    readiness = [
        {"code": code, "label": label, "ready": _strict_bool(readiness_source.get(code))}
        for code, label in READINESS_LABELS
    ]

    raw_warnings = financial.get("warnings")
    raw_warnings = raw_warnings if isinstance(raw_warnings, list) else []
    warnings = []
    warnings_truncated = False
    for item in raw_warnings:
        parsed = _warning_projection(item)
        if parsed is None:
            continue
        if len(warnings) >= MAX_FINANCIAL_WARNINGS:
            warnings_truncated = True
            break
        warnings.append(parsed)

    cash_flow = financial.get("cash_flow")
    cash_flow = cash_flow if isinstance(cash_flow, dict) else {}
    annual_input = financial.get("annual_model_input")
    annual_input = annual_input if isinstance(annual_input, dict) else {}
    forecast = financial.get("next_month_forecast")
    forecast = forecast if isinstance(forecast, dict) else {}
    processing = inner.get("processing")
    processing = processing if isinstance(processing, dict) else {}

    return {
        "is_mock": _strict_bool(financial.get("is_mock")),
        "readiness": readiness,
        "warnings": warnings,
        "warnings_truncated": warnings_truncated,
        "complete_months": _strict_int(cash_flow.get("complete_months")),
        "annual_ready": _strict_bool(annual_input.get("ready")),
        "forecast_ready": _strict_bool(forecast.get("ready")),
        "processing": {
            "duration_ms": _strict_int(processing.get("duration_ms")),
            "llm_chunks": _strict_int(processing.get("llm_chunks")),
        },
        "model_status_message": MODEL_STATUS_MESSAGE,
    }
