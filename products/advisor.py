"""Versioned demo rules; not bank eligibility or approval rules."""

RULES_VERSION = "needs-2"
NEEDS = [
    ("working", "Закупить товар / текущие расходы"),
    ("gap", "Закрыть кассовый разрыв"),
    ("growth", "Открыть или расширить точку"),
    ("equipment", "Купить оборудование или транспорт"),
    ("contract", "Выполнить контракт"),
    ("debt", "Снизить нагрузку по долгам"),
]
DOCUMENTS = {
    "statement": (
        "Выписка по расчётному счёту",
        "Выписка за согласованный с сотрудником период; для предварительного "
        "анализа обычно нужны обороты и поступления за несколько месяцев. "
        "Подойдёт выписка из интернет-банка.",
    ),
    "estimate": (
        "Смета или предложение поставщика",
        "Смета или коммерческое предложение с составом, стоимостью и сроками. "
        "Для оборудования укажите модель, количество и поставщика.",
    ),
    "contract": (
        "Контракт и приложения",
        "Контракт с предметом, суммой, сроками и порядком оплаты. Приложения, "
        "спецификации и графики обязательны, если они являются частью сделки.",
    ),
    "debt": (
        "Кредитные договоры, графики и остатки",
        "Действующие кредитные договоры, графики платежей и справки об остатках "
        "по всем обязательствам, которые планируется пересмотреть.",
    ),
    "collateral": (
        "Документы на обеспечение",
        "Право собственности, оценка или инвентаризация, сведения об обременениях. "
        "Для оборудования и недвижимости подтвердите владельца и характеристики.",
    ),
}
PRODUCT_DOCUMENTS = {
    "working-capital": ["statement"],
    "contract-finance": ["statement", "contract"],
    "investment": ["statement", "estimate"],
    "refinancing": ["statement", "debt"],
}
REFERENCE_ONLY_LIMITATION = (
    "В демо-каталоге нет подключённого продукта для этого варианта: "
    "это справочная информация, подача недоступна."
)
CONSULTATION_MESSAGE = (
    "Автоматический демо-подбор не нашёл доступного продукта. Это не отказ "
    "и не решение банка: обсудите задачу с сотрудником банка."
)
DEMO_DISCLAIMER = "Это не одобрение кредита и не решение банка."


def requirements(product_code, collateral=False):
    codes = list(PRODUCT_DOCUMENTS.get(product_code, ["statement"]))
    if collateral:
        codes.append("collateral")
    return [{"code": code, "name": DOCUMENTS[code][0], "reason": DOCUMENTS[code][1]} for code in codes]


def recommend(data, products):
    need = data.get("need")
    candidates, message = [], "Выберите задачу бизнеса для предварительного подбора."
    if need == "working":
        candidates = [("working-capital", "Оборотный кредит", "Вы финансируете текущие расходы или закупки.")]
    elif need in {"growth", "equipment"}:
        candidates = [("investment", "Инвестиционный кредит", "Деньги нужны на развитие или конкретный актив.")]
        if need == "equipment":
            candidates.append(("leasing", "Лизинг", "Можно сравнить финансирование конкретного актива."))
    elif need == "contract":
        candidates = [("contract-finance", "Контрактный кредит", "Финансирование связано с исполнением контракта.")]
    elif need == "gap":
        kind = data.get("gap_kind")
        if kind == "short":
            candidates = [("overdraft", "Овердрафт", "Ожидается поступление для закрытия краткого разрыва.")]
        elif kind == "receivable":
            candidates = [("factoring", "Факторинг", "Покупатель оплатит уже выполненную поставку с отсрочкой.")]
        elif kind == "recurring":
            candidates = [("credit-line", "Кредитная линия", "Потребность повторяется."), ("working-capital", "Оборотный кредит", "Альтернатива для отдельной закупки; уточните цикл возврата денег.")]
        elif kind == "unknown":
            message = "Нет понятного источника погашения. Нужен разбор с сотрудником; автоматический подбор не выполняется."
        else:
            message = "Уточните характер разрыва и ожидаемое поступление."
    elif need == "debt":
        if data.get("debt_kind") == "loans":
            candidates = [("refinancing", "Рефинансирование", "Вы хотите пересмотреть действующие кредиты без просрочки.")]
        elif data.get("debt_kind"):
            message = "Задолженность поставщикам или просрочка требует отдельного разбора с сотрудником. Рефинансирование автоматически не предлагается."
        else:
            message = "Уточните, какие обязательства вы хотите погасить."

    catalog = {p.code: p for p in products if p.active}
    result = []
    for code, name, reason in candidates:
        product = catalog.get(code)
        implemented = code in PRODUCT_DOCUMENTS
        limitations = []
        if product is None or not implemented:
            available = False
            limitation = REFERENCE_ONLY_LIMITATION
        else:
            if data.get("amount") is not None and not product.min_amount <= data["amount"] <= product.max_amount:
                limitations.append("Сумма вне демо-диапазона продукта.")
            if data.get("term_months") is not None and not product.min_term_months <= data["term_months"] <= product.max_term_months:
                limitations.append("Срок вне демо-диапазона продукта.")
            available = not limitations
            limitation = " ".join(limitations)
        conditions = (f"Демо-диапазон: {product.min_amount:,.0f}–{product.max_amount:,.0f} ₽, "
                      f"{product.min_term_months}–{product.max_term_months} мес. Индивидуальные условия определяет банк.") if product else ""
        result.append({"code": code, "name": name, "reason": reason, "available": available, "conditions": conditions,
                       "limitation": limitation})

    if result and any(item["available"] for item in result):
        message = f"Предварительный подбор по вашей задаче. {DEMO_DISCLAIMER}"
    elif result:
        message = CONSULTATION_MESSAGE
    else:
        message = f"{message} {CONSULTATION_MESSAGE}"
    return {"rules_version": RULES_VERSION, "items": result, "message": message}
