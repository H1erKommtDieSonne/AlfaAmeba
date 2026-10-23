"""Versioned demo rules; not bank eligibility or approval rules."""

RULES_VERSION = "needs-1"
NEEDS = [
    ("working", "Закупить товар / текущие расходы"),
    ("gap", "Закрыть кассовый разрыв"),
    ("growth", "Открыть или расширить точку"),
    ("equipment", "Купить оборудование или транспорт"),
    ("contract", "Выполнить контракт"),
    ("debt", "Снизить нагрузку по долгам"),
]
DOCUMENTS = {
    "statement": ("Выписка по расчётному счёту", "Подтвердить обороты и поступления. Период уточняет сотрудник."),
    "estimate": ("Смета или предложение поставщика", "Подтвердить состав и стоимость проекта или оборудования."),
    "contract": ("Контракт и приложения", "Подтвердить предмет сделки, сроки исполнения и оплаты."),
    "debt": ("Кредитные договоры, графики и остатки", "Сопоставить действующие обязательства и платежи."),
    "collateral": ("Документы на обеспечение", "Подтвердить право собственности на предлагаемый залог."),
}
PRODUCT_DOCUMENTS = {
    "working-capital": ["statement"],
    "contract-finance": ["statement", "contract"],
    "investment": ["statement", "estimate"],
    "refinancing": ["statement", "debt"],
}


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
    catalog = {p.code: p for p in products}
    result = []
    for code, name, reason in candidates:
        product = catalog.get(code)
        available = bool(product and product.active and code in PRODUCT_DOCUMENTS)
        limitation = ""
        if product and data.get("amount") is not None and not product.min_amount <= data["amount"] <= product.max_amount:
            available, limitation = False, "Сумма вне диапазона продукта."
        if product and data.get("term_months") is not None and not product.min_term_months <= data["term_months"] <= product.max_term_months:
            available, limitation = False, "Срок вне диапазона продукта."
        conditions = (f"Демо-диапазон: {product.min_amount:,.0f}–{product.max_amount:,.0f} ₽, "
                      f"{product.min_term_months}–{product.max_term_months} мес. Индивидуальные условия определяет банк.") if product else ""
        result.append({"code": code, "name": name, "reason": reason, "available": available, "conditions": conditions,
                       "limitation": limitation or ("" if available else "Пока только справочная рекомендация; подача недоступна.")})
    return {"rules_version": RULES_VERSION, "items": result, "message": message if not result else "Предварительный подбор по вашей задаче. Это не одобрение кредита."}
