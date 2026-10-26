"""Безопасное представление необязательного ``result.final_analysis``.

Блок приходит от узла рядом с ``financial_analysis`` (не внутри строгой схемы
v2) и содержит текст, сформированный нейросетью. Модуль только читает известные
поля по белому списку, ограничивает размеры строк/списков и никогда не выводит
сырые исключения, промпты или неизвестные ключи. HTML экранирует шаблон;
``mark_safe`` здесь не используется. Отсутствие блока — не ошибка: карточка
показывает фиксированное «не выполнялся» и сохраняет остальные результаты.
"""

SCHEMA_VERSION = 1
PROMPT_VERSION = "final-analysis-1"

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_NOT_RUN = "not_run"

MAX_SUMMARY_CHARS = 2000
MAX_LIST_ITEMS = 8
MAX_LIST_ITEM_CHARS = 500
MAX_MODEL_CHARS = 120
MAX_PROMPT_CHARS = 64
MAX_HASH_CHARS = 128
MAX_TIME_CHARS = 40

LIST_FIELDS = (
    "key_points",
    "risks",
    "missing",
    "conflicts",
    "next_steps",
    "limitations",
)

DISCLAIMER = "Текст сформирован нейросетью по документам заявки. Это не решение банка."
ABSENT_MESSAGE = "Для этой обработки итоговый анализ нейросети не выполнялся."
FAILED_MESSAGE = (
    "Итоговый анализ нейросети не выполнен из-за технической ошибки. "
    "Это не решение банка."
)
NOT_RUN_MESSAGE = ABSENT_MESSAGE
INVALID_MESSAGE = "Итоговый анализ нейросети недоступен. Это не решение банка."
# Отдельные честные статусы, когда блока нет из-за состояния обработки.
EXTRACTION_ERROR_MESSAGE = (
    "Итоговый анализ не запускался: не удалось обработать документы."
)
PENDING_MESSAGE = (
    "Выполняется обработка заявки. Результат появится после завершения."
)
NO_JOB_MESSAGE = (
    "Обработка заявки ещё не запускалась. Результат появится после её завершения."
)
JOB_FAILED_MESSAGE = (
    "Обработка заявки завершилась технической ошибкой. "
    "Итоговый анализ нейросети не выполнялся."
)


def _text(value, limit):
    """Непустая обрезанная строка или ``""``; не строки не считаются текстом."""
    if not isinstance(value, str):
        return ""
    return value.replace("\x00", " ").strip()[:limit]


def _items(value):
    """До 8 непустых строк по 500 символов; мусор и лишнее отбрасываем."""
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        if len(items) >= MAX_LIST_ITEMS:
            break
        text = _text(item, MAX_LIST_ITEM_CHARS)
        if text:
            items.append(text)
    return items


def _provenance(value):
    """Только известные строковые поля провенанса; неизвестный промпт скрываем."""
    if not isinstance(value, dict):
        return None
    provenance = {
        "model": _text(value.get("model"), MAX_MODEL_CHARS),
        "prompt_version": _text(value.get("prompt_version"), MAX_PROMPT_CHARS),
        "generated_at": _text(value.get("generated_at"), MAX_TIME_CHARS),
        "evidence_hash": _text(value.get("evidence_hash"), MAX_HASH_CHARS),
    }
    if provenance["prompt_version"] not in ("", PROMPT_VERSION):
        provenance["prompt_version"] = ""
    provenance = {key: text for key, text in provenance.items() if text}
    return provenance or None


def _blank(state, message, is_mock=None):
    result = {
        "state": state,
        "message": message,
        "is_mock": is_mock if isinstance(is_mock, bool) else None,
        "summary": "",
        "provenance": None,
        "not_a_decision": True,
        "disclaimer": DISCLAIMER,
    }
    result.update({field: [] for field in LIST_FIELDS})
    return result


def final_analysis_placeholder(message, is_mock=None):
    """Безопасная заглушка ``not_run`` с фиксированным текстом состояния."""
    return _blank(STATUS_NOT_RUN, message, is_mock)


def final_analysis_view(inner):
    """Безопасный словарь для шаблона по вложенному ``result`` callback.

    ``state``: ``completed`` — можно показывать текст нейросети; ``failed`` /
    ``not_run`` — фиксированные сообщения; ``invalid`` — блок есть, но не
    прошёл проверку. Сырой блок и незнакомые поля наружу не попадают.
    """
    if not isinstance(inner, dict) or "final_analysis" not in inner:
        return _blank("not_run", ABSENT_MESSAGE)
    raw = inner.get("final_analysis")
    if raw is None:
        return _blank("not_run", ABSENT_MESSAGE)
    if not isinstance(raw, dict):
        return _blank("invalid", INVALID_MESSAGE)

    schema = raw.get("schema_version")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != SCHEMA_VERSION:
        return _blank("invalid", INVALID_MESSAGE)

    status = raw.get("status")
    is_mock = raw.get("is_mock")
    if status == STATUS_FAILED:
        return _blank("failed", FAILED_MESSAGE, is_mock)
    if status == STATUS_NOT_RUN:
        return _blank("not_run", NOT_RUN_MESSAGE, is_mock)
    if status != STATUS_COMPLETED or raw.get("not_a_decision") is not True:
        return _blank("invalid", INVALID_MESSAGE, is_mock)

    summary = _text(raw.get("summary"), MAX_SUMMARY_CHARS)
    lists = {field: _items(raw.get(field)) for field in LIST_FIELDS}
    if not summary and not any(lists.values()):
        return _blank("invalid", INVALID_MESSAGE, is_mock)

    result = _blank("completed", "", is_mock)
    result["summary"] = summary
    result.update(lists)
    result["provenance"] = _provenance(raw.get("provenance"))
    return result
