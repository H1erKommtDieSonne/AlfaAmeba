"""Какой раздел кабинета открыт сейчас — для подсветки пункта в шапке.

Подсвечиваем весь раздел, а не одну страницу: из списка заявок человек ушёл в карточку,
но из «Моих заявок» не вышел. Раздел определяется по имени урла, а не по пути: путь можно
поменять, а имя живёт в шаблонах и в `reverse`, и разойтись они не могут.
"""

SECTIONS = {
    "business": {"business-cabinet", "application-success"},
    "business-applications": {"business-applications", "business-application-detail"},
    "bank": {"bank-queue", "bank-application-detail"},
    "bank-admin": {"bank-admin-dashboard"},
}

# Имя урла → раздел. Разворачиваем один раз при импорте, а не на каждый запрос.
_BY_URL = {name: section for section, names in SECTIONS.items() for name in names}


def section(request):
    """Имя раздела для этого запроса или пустая строка."""
    match = getattr(request, "resolver_match", None)
    return _BY_URL.get(match.url_name, "") if match else ""
