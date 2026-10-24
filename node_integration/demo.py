"""Синтетический callback внешнего узла со схемой v2 — для демо-стенда и тестов карточки.

Ничего не отправляет и узел не заменяет: собирает правдоподобный результат по тем же
правилам, что описаны в ``docs/financial-api-v2.md`` (среднее трёх последних полных
месяцев, сценарии 1.0/1.0, 0.9/1.1, 0.8/1.2, мост до целевого месяца). Все данные
вымышлены и помечены ``is_mock``. Соответствие схеме проверяет
``node_integration.test_financial``.
"""

SCENARIOS = (("base", 1.0, 1.0), ("cautious", 0.9, 1.1), ("stress", 0.8, 1.2))
ANALYSIS_DATE = "2026-10-24"
ACCOUNT = "40702810900000012345"
STATEMENT = "vypiska_apr-sen_2026.pdf"
REPORTING = "otchetnost_2025.pdf"
CONTRACT = "kontrakt_vektor.pdf"

# Месяц, поступления от покупателей, операционные платежи, обслуживание долга,
# прочие поступления (они же уходят переводом между своими счетами), полнота, рубли.
WORKING_MONTHS = (
    ("2026-04", 4_200_000, 3_100_000, 350_000, 0, True),
    ("2026-05", 4_600_000, 3_300_000, 350_000, 500_000, True),
    ("2026-06", 3_900_000, 3_500_000, 350_000, 0, True),
    ("2026-07", 5_100_000, 3_600_000, 350_000, 0, True),
    ("2026-08", 4_800_000, 3_400_000, 350_000, 300_000, True),
    ("2026-09", 5_400_000, 3_800_000, 350_000, 0, True),
)
CONTRACT_MONTHS = (
    ("2026-08", 2_100_000, 1_900_000, 120_000, 0, True),
    ("2026-09", 2_600_000, 2_200_000, 120_000, 0, True),
    ("2026-10", 900_000, 1_100_000, 0, 0, False),
)
BUYER_SHARES = (
    ("ООО Ромашка", 0.42),
    ("ИП Сидоров А. В.", 0.23),
    ("ООО Вектор", 0.18),
    ("АО Северный опт", 0.10),
    ("ООО Лидер", 0.04),
    ("ИП Котова Е. Н.", 0.03),
)
WORKING_ANNUAL = {
    "revenue": (58000.0, "2110", "Выручка"),
    "net_profit_current": (3900.0, "2400", "Чистая прибыль (убыток)"),
    "operating_profit_current": (5200.0, "2200", "Прибыль (убыток) от продаж"),
    "total_assets": (41000.0, "1600", "Баланс"),
    "equity": (16500.0, "1300", "Итого по разделу III"),
    "current_assets": (27500.0, "1200", "Итого по разделу II"),
    "short_term_liabilities": (19800.0, "1500", "Итого по разделу V"),
    "cash": (4100.0, "1250", "Денежные средства и денежные эквиваленты"),
    "receivables": (11200.0, "1230", "Дебиторская задолженность"),
}


def _pdf_escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def demo_pdf(*lines):
    """Одностраничный PDF с текстом — для синтетических демо-документов.

    Стандартный шрифт Helvetica кириллицы не знает, поэтому строки — латиницей.
    """
    text = "BT /F1 12 Tf 50 780 Td 18 TL " + " ".join(f"({_pdf_escape(line)}) Tj T*" for line in lines) + " ET"
    stream = text.encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(output))
        output += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    output += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    output += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return output


# Синтетические файлы демо-заявок: те же имена, что в демо-ответе узла.
WORKING_DOCUMENTS = (
    (STATEMENT, "statement", ("DEMO bank statement (synthetic data)", f"Account {ACCOUNT}", "Period 01.04.2026 - 30.09.2026")),
    (REPORTING, "", ("DEMO financial statements 2025 (synthetic data)", "Line 2110 Revenue 58 000", "Line 1600 Balance 41 000")),
)
CONTRACT_DOCUMENTS = (
    (CONTRACT, "contract", ("DEMO contract 44-2026/117 (synthetic data)", "Dated 01.12.2024", "Page 3 is intentionally unreadable")),
    (STATEMENT, "statement", ("DEMO bank statement (synthetic data)", f"Account {ACCOUNT}", "Period 01.08.2026 - 31.10.2026")),
)


def _source(filename, locator, quote="", provider=None):
    return {"filename": filename, "provider": provider, "locator": locator, "quote": quote, "is_mock": True}


def _months(rows, opening):
    months, balance = [], opening
    for month, receipts, operating, debt, transfers, complete in rows:
        closing = balance + receipts + transfers - operating - debt - transfers
        months.append({
            "month": month,
            "complete": complete,
            "unit": "RUB",
            "accounts": [ACCOUNT],
            "sources": [_source(STATEMENT, f"выписка, итоги за {month}", f"Обороты за период {month}")],
            "opening_balance": float(balance),
            "closing_balance": float(closing),
            "inflows": float(receipts + transfers),
            "outflows": float(operating + debt + transfers),
            "customer_receipts": float(receipts),
            "operating_payments": float(operating),
            "debt_service": float(debt),
            "customer_breakdown": {name: round(receipts * share, 2) for name, share in BUYER_SHARES},
        })
        balance = closing
    return months


def _cash_flow(months):
    complete = [month for month in months if month["complete"]]
    count = len(complete)

    def average(key):
        return round(sum(month[key] for month in complete) / count, 2) if count else None

    last = complete[-1] if complete else None
    return {
        "unit": "RUB",
        "currency": "RUB",
        "months": months,
        "complete_months": count,
        "accounts_confirmed": True,
        "average_monthly_inflows_rub": average("inflows"),
        "average_monthly_outflows_rub": average("outflows"),
        "average_month_end_balance_rub": average("closing_balance"),
        "latest_observed_balance_rub": last["closing_balance"] if last else None,
        "latest_observed_balance_date": f"{last['month']}-30" if last else None,
    }


def _forecast(months, *, existing_debt, new_payment):
    complete = [month for month in months if month["complete"]]
    limitations = [
        "Сценарии — заданные изменения ±10/20%, а не вероятности или доверительные интервалы",
        "Учитываются поступления покупателей, операционные платежи и известные платежи по долгу; иные будущие потоки предполагаются нулевыми",
        "Конечный месячный остаток не исключает внутримесячный кассовый разрыв",
        "Банковские поступления не равны бухгалтерской выручке",
    ]
    base = {"ready": False, "period": "2026-11", "forecast_as_of": ANALYSIS_DATE, "data_as_of": None,
            "horizon_months_after_last_observation": None, "unit": "RUB",
            "method": "mean_last_3_complete_consecutive_months", "scenarios": [],
            "backtest": {"folds": 0, "mae_rub": None, "mape_percent": None},
            "cash_gap_probability": None, "confidence_interval": None, "limitations": limitations}
    if len(complete) < 3:
        return base
    last3 = complete[-3:]
    receipts = sum(month["customer_receipts"] for month in last3) / 3
    operating = sum(month["operating_payments"] for month in last3) / 3
    start = complete[-1]["closing_balance"]
    scenarios = []
    for name, receipts_factor, payments_factor in SCENARIOS:
        month_receipts = round(receipts * receipts_factor, 2)
        month_operating = round(operating * payments_factor, 2)
        bridge = round(start + month_receipts - month_operating - existing_debt, 2)
        after_existing = round(bridge + month_receipts - month_operating - existing_debt, 2)
        after_all = round(after_existing - new_payment, 2)
        scenarios.append({
            "name": name,
            "assumptions": {"receipts_factor": receipts_factor, "operating_payments_factor": payments_factor},
            "customer_receipts_rub": month_receipts,
            "operating_payments_rub": month_operating,
            "existing_debt_service_rub": float(existing_debt),
            "proposed_credit_payment_rub": float(new_payment),
            "starting_observed_balance_rub": start,
            "opening_balance_rub": bridge,
            "balance_after_existing_debt_rub": after_existing,
            "balance_after_all_debt_rub": after_all,
            "cash_gap_rub": round(max(0.0, -after_all), 2),
            "projection_path": [
                {"month": "2026-10", "opening_balance_rub": start, "customer_receipts_rub": month_receipts,
                 "operating_payments_rub": month_operating, "existing_debt_service_rub": float(existing_debt),
                 "proposed_credit_payment_rub": 0.0, "closing_balance_rub": bridge},
                {"month": "2026-11", "opening_balance_rub": bridge, "customer_receipts_rub": month_receipts,
                 "operating_payments_rub": month_operating, "existing_debt_service_rub": float(existing_debt),
                 "proposed_credit_payment_rub": float(new_payment), "closing_balance_rub": after_all},
            ],
        })
    return {**base, "ready": True, "data_as_of": f"{complete[-1]['month']}-30",
            "horizon_months_after_last_observation": 2, "scenarios": scenarios,
            "backtest": {"folds": 3, "mae_rub": 412000.0, "mape_percent": 8.6}}


def _statistic(value, formula, unit="ratio", sources=None, reason=None, inputs=None):
    available = value is not None
    return {
        "value": round(value, 6) if available else None,
        "unit": unit,
        "formula": formula,
        "status": "available" if available else "unavailable",
        "reason": None if available else (reason or "Нет согласованных входов либо знаменатель неположительный"),
        "inputs": inputs,
        "sources": sources or [],
    }


def _statistics(annual, months, *, ebitda=None, total_debt=None, cfads=None, debt_service=None):
    def metric(key):
        return annual.get(key)

    def ratio(numerator, denominator):
        if numerator is None or not denominator:
            return None
        return numerator / denominator

    totals = {}
    for month in months:
        for name, amount in month["customer_breakdown"].items():
            totals[name] = totals.get(name, 0) + amount
    largest = ratio(max(totals.values()), sum(totals.values())) if totals else None
    reporting = [_source(REPORTING, "форма 0710001, раздел V", "Итого по разделу V")]
    return {
        "current_ratio": _statistic(ratio(metric("current_assets"), metric("short_term_liabilities")), "current_assets / short_term_liabilities"),
        "cash_ratio": _statistic(ratio(metric("cash"), metric("short_term_liabilities")), "cash / short_term_liabilities"),
        "equity_to_assets": _statistic(ratio(metric("equity"), metric("total_assets")), "equity / total_assets"),
        "net_margin": _statistic(ratio(metric("net_profit_current"), metric("revenue")), "net_profit_current / revenue"),
        "operating_margin": _statistic(ratio(metric("operating_profit_current"), metric("revenue")), "operating_profit_current / revenue"),
        "debt_to_ebitda": _statistic(ratio(total_debt, ebitda), "total_debt / ebitda", sources=reporting if ebitda else None,
                                     inputs={"total_debt_rub": total_debt * 1000 if total_debt else None, "ebitda_rub": ebitda * 1000 if ebitda else None}),
        "dscr": _statistic(ratio(cfads, debt_service), "cfads / debt_service_same_period", sources=reporting if cfads else None,
                           inputs={"cfads_rub": cfads * 1000 if cfads else None, "debt_service_rub": debt_service * 1000 if debt_service else None,
                                   "period_start": "2025-01-01", "period_end": "2025-12-31"}),
        "collateral_value_rub": _statistic(None, "latest sourced valuation", unit="RUB"),
        "largest_customer_receipts_share": _statistic(largest, "largest counterparty customer receipts / all classified customer receipts"),
    }


def _annual_input(values, missing=()):
    metrics = {key: (None if key in missing else value) for key, (value, _, _) in values.items()}
    metrics["okved_group"] = "46"
    sources = {
        key: [_source(REPORTING, f"страница 2, строка {line}", f"{line} {caption} {value:,.0f}".replace(",", " "))]
        for key, (value, line, caption) in values.items() if key not in missing
    }
    sources["okved_group"] = [_source(None, "metrics.okved_group", provider="mock-bo")]
    return {
        "metrics": metrics,
        "unit": "RUB_THOUSANDS",
        "currency": "RUB",
        "period_start": "2025-01-01",
        "period_end": "2025-12-31",
        "as_of": "2025-12-31",
        "ready": not missing,
        "missing_fields": list(missing),
        "sources": sources,
    }


def _request(code, title, message, required_for, priority="required", fields=(), filename=None, action="upload_or_clarify"):
    return {"code": code, "title": title, "message": message, "fields": list(fields), "required_for": list(required_for),
            "priority": priority, "action": action, "filename": filename}


def _model_outputs():
    return {
        "status": "not_run",
        "forecast_revenue_rub_thousands": None, "safe_credit_limit_rub_thousands": None,
        "net_margin_percent": None, "operating_margin_percent": None, "loss_probability_percent": None,
        "liquidity_crisis_probability_percent": None, "default_probability_percent": None,
        "approval_probability_percent": None, "risk_grade": None, "decision": None,
        "operating_cash_flow_forecast_rub": None,
        "note": "Годовые модели запускаются отдельно по annual_model_input.metrics; вероятность убытка/дефицита ликвидности не является вероятностью дефолта или одобрения.",
    }


def _application(application_id, version, inn, loan_type, amount, term, collateral):
    return {"application_id": str(application_id), "document_version": version, "borrower_inn": inn,
            "loan_type": loan_type, "loan_amount_rub": float(amount), "term_months": term,
            "annual_rate_percent": None, "has_collateral": collateral, "analysis_date": ANALYSIS_DATE}


def monthly_payment(amount, term_months, annual_rate=0.18):
    """Аннуитетный платёж для демо: ставку банк в контекст пока не передаёт."""
    rate = annual_rate / 12
    return round(float(amount) * rate / (1 - (1 + rate) ** -term_months), 2)


def working_capital_result(*, application_id, version=1, inn="7707083893", amount=15_000_000, term=24, payment=None):
    """Комплект собран: выписка за полгода, отчётность, прогноз по всем сценариям.

    ``payment`` — платёж, подтверждённый сотрудником; без него — аннуитет по демо-ставке.
    """
    months = _months(WORKING_MONTHS, opening=6_000_000)
    confirmed = payment is not None
    payment = float(payment) if confirmed else monthly_payment(amount, term)
    annual_values = {key: value for key, (value, _, _) in WORKING_ANNUAL.items()}
    analysis = {
        "schema_version": 2,
        "application": _application(application_id, version, inn, "working_capital", amount, term, False),
        "annual_model_input": _annual_input(WORKING_ANNUAL),
        "cash_flow": _cash_flow(months),
        "statistics": _statistics(annual_values, months, ebitda=6900.0, total_debt=9800.0, cfads=6100.0, debt_service=4200.0),
        "next_month_forecast": _forecast(months, existing_debt=350_000, new_payment=payment),
        "model_outputs": _model_outputs(),
        "document_fields": [
            {"name": "account_number", "value": ACCOUNT, "unit": "CODE", "source": _source(STATEMENT, "страница 1, шапка", f"Счёт № {ACCOUNT}")},
            {"name": "statement_period", "value": "01.04.2026 — 30.09.2026", "unit": "TEXT", "source": _source(STATEMENT, "страница 1, шапка", "за период с 01.04.2026 по 30.09.2026")},
            {"name": "ebitda", "value": 6900.0, "unit": "RUB_THOUSANDS", "source": _source(REPORTING, "пояснения, таблица 4", "EBITDA 6 900")},
        ],
        "client_requests": [
            _request("seasonality_history", "Выписка за 24 месяца",
                     "Для оценки сезонности нужна история поступлений минимум за 24 последовательных полных месяца.",
                     ["monthly_cash_flow"], priority="optional"),
        ],
        "employee_recommendations": [
            {"code": "verify_extraction", "message": "Сверьте извлечённые показатели с указанными источниками; автоматическое извлечение не подтверждает подлинность документов."},
            {"code": "confirm_payment", "message": (
                f"В расчёте платёж по новому кредиту — {payment:,.0f} ₽, подтверждённый сотрудником."
                if confirmed else
                f"В расчёте платёж по новому кредиту — {payment:,.0f} ₽ (демо-ставка 18 %). Подтвердите платёж из расчёта банка."
            ).replace(",", " ")},
        ],
        "warnings": [
            {"code": "customer_concentration", "severity": "medium", "message": "Высокая концентрация на одном покупателе: 42 % поступлений.", "fields": ["customer_breakdown"]},
            {"code": "operating_costs_growth", "severity": "low", "message": "Операционные платежи выросли на 23 % за полгода.", "fields": ["operating_payments"]},
            {"code": "seasonality_not_estimated", "severity": "low", "message": "Базовый прогноз не моделирует годовую сезонность; нужна история минимум за 24 месяца.", "fields": []},
            {"code": "mock_data", "severity": "medium", "message": "В наборе демонстрационные данные; не использовать как реальные сведения клиента.", "fields": []},
        ],
        "readiness": {"annual_model": True, "monthly_cash_flow": True, "next_month_receipts_and_operating_payments": True,
                      "balance_after_all_debt": True, "dscr": True, "debt_to_ebitda": True, "collateral_valuation": False},
        "is_mock": True,
    }
    files = [
        {"filename": STATEMENT, "method": "pdf_text", "truncated": False, "uncertain_fragments": [], "verified": False,
         "status": "checked", "sha256": "0" * 64, "categories": ["bank_statement"]},
        {"filename": REPORTING, "method": "pdf_text", "truncated": False, "uncertain_fragments": [], "verified": False,
         "status": "checked", "sha256": "1" * 64, "categories": ["financial_statements"]},
    ]
    return _callback("complete", analysis, files, issues=[], verification="not_verified")


def contract_result(*, application_id, version=1, inn="7707083893", amount=8_000_000, term=12, payment=None):
    """Контрактный кредит с залогом: нет контракта целиком, нет оценки, выписка короткая."""
    months = _months(CONTRACT_MONTHS, opening=1_400_000)
    annual = dict(WORKING_ANNUAL)
    missing = ("receivables",)
    annual_values = {key: value for key, (value, _, _) in annual.items() if key not in missing}
    analysis = {
        "schema_version": 2,
        "application": _application(application_id, version, inn, "contract", amount, term, True),
        "annual_model_input": _annual_input(annual, missing=missing),
        "cash_flow": _cash_flow(months),
        "statistics": _statistics(annual_values, months),
        "next_month_forecast": _forecast(months, existing_debt=120_000,
                                         new_payment=float(payment) if payment is not None else monthly_payment(amount, term)),
        "model_outputs": _model_outputs(),
        "document_fields": [
            {"name": "contract_number", "value": "44-2026/117", "unit": "CODE", "source": _source(CONTRACT, "страница 1", "Контракт № 44-2026/117")},
            {"name": "contract_date", "value": "2024-12-01", "unit": "DATE", "source": _source(CONTRACT, "страница 1", "от 01.12.2024")},
        ],
        "client_requests": [
            _request("unreadable_page", "Читаемая версия контракта",
                     "Страница 3 не распознана. Пришлите оригинал PDF или более качественный скан.",
                     ["product_document_check"], filename=CONTRACT, action="review_or_replace"),
            _request("document_contract_appendix", "Приложение к контракту",
                     "Нужно приложение № 1 со спецификацией, графиком поставок и ценами.", ["product_document_check"]),
            _request("collateral_valuation_missing", "Оценка обеспечения",
                     "Предоставьте оценочный отчёт с датой и стоимостью обеспечения; право собственности само по себе не подтверждает стоимость.",
                     ["collateral_valuation"], fields=["collateral_value"]),
            _request("statement_history", "Выписка за три полных месяца",
                     "Для прогноза нужна выписка минимум за три последовательных полных месяца по всем счетам.", ["monthly_cash_flow"]),
            _request("dscr_inputs_missing", "Данные для DSCR",
                     "Предоставьте денежный поток, доступный для обслуживания долга (CFADS), и полный график платежей за тот же период.",
                     ["dscr"], priority="optional", fields=["cfads", "debt_service"]),
        ],
        "employee_recommendations": [
            {"code": "verify_contract_date", "message": "Контракт датирован 01.12.2024 — уточните у клиента, действует ли он сейчас."},
            {"code": "verify_extraction", "message": "Сверьте извлечённые показатели с указанными источниками; автоматическое извлечение не подтверждает подлинность документов."},
        ],
        "warnings": [
            {"code": "unreadable_page", "severity": "high", "message": "Страница 3 контракта нечитаема — данные с неё не использованы.", "fields": []},
            {"code": "short_bank_history", "severity": "medium", "message": "Полных месяцев выписки только два — прогноз остатка не построен.", "fields": ["cash_flow"]},
            {"code": "stale_contract", "severity": "medium", "message": "Дата контракта — 01.12.2024; актуальность не подтверждена.", "fields": ["contract_date"]},
            {"code": "mock_data", "severity": "low", "message": "В наборе демонстрационные данные; не использовать как реальные сведения клиента.", "fields": []},
        ],
        "readiness": {"annual_model": False, "monthly_cash_flow": False, "next_month_receipts_and_operating_payments": False,
                      "balance_after_all_debt": False, "dscr": False, "debt_to_ebitda": False, "collateral_valuation": False},
        "is_mock": True,
    }
    files = [
        {"filename": CONTRACT, "method": "pdf_ocr", "truncated": False, "uncertain_fragments": ["страница 3"], "verified": False,
         "status": "checked", "sha256": "2" * 64, "categories": ["contract"]},
        {"filename": STATEMENT, "method": "pdf_text", "truncated": False, "uncertain_fragments": [], "verified": False,
         "status": "checked", "sha256": "3" * 64, "categories": ["bank_statement"]},
    ]
    issues = [
        {"code": "unreadable_page", "message": "Нечитаемая страница в PDF", "filename": CONTRACT, "locator": "страница 3",
         "suggestion": "Страница содержит неразборчивый текст. Загрузите более качественную скан-копию."},
        {"code": "missing_document", "message": "Отсутствует приложение к контракту",
         "suggestion": "Не найдено приложение № 1 со спецификацией и графиком поставок."},
        {"code": "account_number_unreadable", "message": "Не удалось распознать номер счёта", "filename": STATEMENT, "locator": "страницы 2–3",
         "suggestion": "Номер счёта на некоторых страницах не распознан. Загрузите более чистую выписку."},
        {"code": "missing_field", "message": "Оценка обеспечения. Не получены данные: collateral_value", "field": "collateral_value",
         "suggestion": "Предоставьте оценочный отчёт с датой и стоимостью обеспечения."},
    ]
    return _callback("needs_documents", analysis, files, issues=issues, verification="needs_review")


def _callback(status, analysis, files, *, issues, verification):
    return {
        "status": "completed",
        "verification_status": verification,
        "model": "demo-stub",
        "files": files,
        "result": {
            "schema_version": 2,
            "status": status,
            "summary": "Данные подготовлены. Проверьте готовность каждого вида анализа и запросы дополнительной информации.",
            "issues": issues,
            "missing_documents": [issue["message"] for issue in issues if issue["code"] == "missing_document"],
            "recommendations": [item["message"] for item in analysis["employee_recommendations"]],
            "documents_checked": len(files),
            "financial_analysis": analysis,
            "processing": {"started_at": f"{ANALYSIS_DATE}T10:00:00+00:00", "completed_at": f"{ANALYSIS_DATE}T10:00:04+00:00",
                           "duration_ms": 4100, "files_received": len(files), "accepted_observations": 42, "llm_chunks": 3},
        },
    }
