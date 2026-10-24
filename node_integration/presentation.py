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
