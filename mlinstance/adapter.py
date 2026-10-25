"""Рекомендательный адаптер годовых ML-моделей для узла инференса.

Модуль намеренно лёгкий: на импорте используются только стандартная библиотека
и собственный код. Обученные артефакты и тяжёлые зависимости (``numpy``,
``pandas``, ``scikit-learn``, ``joblib``) сюда не импортируются, пока не будет
вызвана функция, которой их передали явно. Оригинальный
``mlinstance/predict_application.py`` не импортируется и не изменяется: он
загружает joblib на уровне модуля и печатает русские строки, поэтому его нельзя
использовать как библиотеку. Этот адаптер повторяет его расчёт, но возвращает
сырые числовые прогнозы.

Что делает адаптер сам:

* проверяет контракт запроса ``POST /v1/predict`` (``schema_version: 1``);
* принимает от вызывающего ``scorer`` и превращает его сырые прогнозы в блок
  ``model_outputs`` схемы финансового результата v2;
* считает рекомендательный лимит (20 % среднемесячной прогнозной выручки) и
  дисконт 50 % при низкой марже — как в оригинальном скрипте.

Чего адаптер **не** делает: не является решением банка, не обучает и не
загружает модели, не ходит в сеть, не пишет в БД. ``decision`` — это
рекомендация модели для сотрудника, а не кредитное решение. Вероятностей
дефолта (PD) и одобрения у этих пяти моделей нет: соответствующие поля
остаются ``null``.
"""

import math

SCHEMA_VERSION = 1

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

UNIT = "RUB_THOUSANDS"
CURRENCY = "RUB"

# Девять числовых годовых входов и один строковый (okved_group).
NUMERIC_METRIC_FIELDS = (
    "revenue",
    "net_profit_current",
    "operating_profit_current",
    "total_assets",
    "equity",
    "current_assets",
    "short_term_liabilities",
    "cash",
    "receivables",
)
# Обороты и балансовые суммы не бывают отрицательными; прибыль и капитал — могут.
NONNEGATIVE_METRIC_FIELDS = frozenset(
    {"revenue", "total_assets", "current_assets", "short_term_liabilities", "cash", "receivables"}
)
OKVED_FIELD = "okved_group"

# Сырые прогнозы, которые обязан вернуть ``scorer``: ровно пять обученных моделей.
RAW_OUTPUT_FIELDS = (
    "forecast_revenue_rub_thousands",
    "net_margin_percent",
    "operating_margin_percent",
    "loss_probability_percent",
    "liquidity_crisis_probability_percent",
)
# Только это — вероятности, они обязаны быть в диапазоне 0..100 %.
PROBABILITY_OUTPUT_FIELDS = ("loss_probability_percent", "liquidity_crisis_probability_percent")

# Слоты схемы v2, для которых у пяти моделей нет обученных значений.
UNSUPPORTED_NULL_FIELDS = (
    "default_probability_percent",
    "approval_probability_percent",
    "risk_grade",
    "operating_cash_flow_forecast_rub",
)

# Правила лимита и рекомендации — перенесены из оригинального скрипта.
MONTHLY_LIMIT_SHARE = 0.20
MONTHS_PER_YEAR = 12
LOSS_PROBABILITY_STOP = 40.0
LIQUIDITY_PROBABILITY_STOP = 50.0
LOW_MARGIN_PERCENT = 1.0
DISCOUNT_FACTOR = 0.5

DECISION_ADVISORY_NOTE = (
    "Рекомендация модели для сотрудника, а не решение банка. Вероятность убытка и дефицита "
    "ликвидности не является вероятностью дефолта или одобрения."
)
FAILED_NOTE = (
    "Расчёт модели не выполнен. Это технический статус, не замечание к документам клиента "
    "и не решение банка."
)

_APPROVE_DECISION = "ОДОБРЕНО (рекомендация модели)"
_DISCOUNT_DECISION = "ОДОБРЕНО С ДИСКОНТОМ 50% (рекомендация модели): низкая маржинальность"
_LOSS_DECISION = "ОТКАЗ (рекомендация модели): высокая вероятность убытка"
_LIQUIDITY_DECISION = "ОТКАЗ (рекомендация модели): риск кассового разрыва (дефицит ликвидности)"


def _is_finite_number(value):
    """``bool`` — подкласс ``int``, но числом отчётности не бывает."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_request(request):
    """Проверяет контракт ``POST /v1/predict``.

    Возвращает ``(application_id, document_version, is_mock, metrics)``.
    Нарушение контракта — ``ValueError`` (узел отвечает HTTP 422).
    """
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    if request.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")

    is_mock = request.get("is_mock")
    if not isinstance(is_mock, bool):
        raise ValueError("is_mock must be a boolean")

    application_id = request.get("application_id")
    if isinstance(application_id, bool) or not isinstance(application_id, (str, int)):
        raise ValueError("application_id must be a string or integer")
    document_version = request.get("document_version")
    if isinstance(document_version, bool) or not isinstance(document_version, int):
        raise ValueError("document_version must be an integer")

    annual = request.get("annual_model_input")
    if not isinstance(annual, dict):
        raise ValueError("annual_model_input must be an object")
    if annual.get("unit") != UNIT:
        raise ValueError(f"annual_model_input.unit must be {UNIT!r}")
    if annual.get("currency") != CURRENCY:
        raise ValueError(f"annual_model_input.currency must be {CURRENCY!r}")
    if annual.get("ready") is not True:
        raise ValueError("annual_model_input.ready must be true")
    missing = annual.get("missing_fields")
    if not isinstance(missing, list) or missing:
        raise ValueError("annual_model_input.missing_fields must be an empty list")

    metrics = annual.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("annual_model_input.metrics must be an object")
    for name in NUMERIC_METRIC_FIELDS:
        value = metrics.get(name)
        if not _is_finite_number(value):
            raise ValueError(f"metrics.{name} must be a finite number")
        if name in NONNEGATIVE_METRIC_FIELDS and value < 0:
            raise ValueError(f"metrics.{name} must be non-negative")

    okved = metrics.get(OKVED_FIELD)
    if not isinstance(okved, str) or len(okved.strip()) < 2 or not okved.strip()[:2].isdigit():
        raise ValueError("metrics.okved_group must be a string starting with two digits")

    return application_id, document_version, is_mock, metrics


def _validate_raw_outputs(raw):
    if not isinstance(raw, dict):
        raise ValueError("scorer must return an object")
    clean = {}
    for name in RAW_OUTPUT_FIELDS:
        value = raw.get(name)
        if not _is_finite_number(value):
            raise ValueError(f"scorer output {name} must be a finite number")
        clean[name] = float(value)
    for name in PROBABILITY_OUTPUT_FIELDS:
        if not 0.0 <= clean[name] <= 100.0:
            raise ValueError(f"scorer output {name} must be within 0..100 percent")
    # Выручка не бывает отрицательной (схема и безопасный лимит ждут неотрицательное);
    # ноль — законное значение.
    if clean["forecast_revenue_rub_thousands"] < 0:
        raise ValueError("scorer output forecast_revenue_rub_thousands must be non-negative")
    return clean


def _safe_credit_limit(forecast_revenue_rub_thousands):
    """20 % среднемесячной прогнозной выручки, как в оригинальном скрипте."""
    return MONTHLY_LIMIT_SHARE * (forecast_revenue_rub_thousands / MONTHS_PER_YEAR)


def _decision(raw):
    """Возвращает ``(текст рекомендации, множитель дисконта либо None)``."""
    if raw["loss_probability_percent"] > LOSS_PROBABILITY_STOP:
        return _LOSS_DECISION, None
    if raw["liquidity_crisis_probability_percent"] > LIQUIDITY_PROBABILITY_STOP:
        return _LIQUIDITY_DECISION, None
    if raw["net_margin_percent"] < LOW_MARGIN_PERCENT:
        return _DISCOUNT_DECISION, DISCOUNT_FACTOR
    return _APPROVE_DECISION, None


def assemble_model_outputs(raw):
    """Сырые прогнозы ``scorer`` → блок ``model_outputs`` схемы v2.

    Недопустимые значения (не-число, ``NaN``/``inf``, вероятность вне 0..100,
    отрицательная прогнозная выручка) поднимают ``ValueError``: вызывающий код
    решает, ответить 422 или ``failed``. Ноль — законное значение.
    """
    raw = _validate_raw_outputs(raw)
    decision, discount = _decision(raw)
    safe_limit = _safe_credit_limit(raw["forecast_revenue_rub_thousands"])
    if discount is not None:
        safe_limit *= discount

    outputs = {
        "status": STATUS_COMPLETED,
        "forecast_revenue_rub_thousands": raw["forecast_revenue_rub_thousands"],
        "safe_credit_limit_rub_thousands": safe_limit,
        "net_margin_percent": raw["net_margin_percent"],
        "operating_margin_percent": raw["operating_margin_percent"],
        "loss_probability_percent": raw["loss_probability_percent"],
        "liquidity_crisis_probability_percent": raw["liquidity_crisis_probability_percent"],
        "decision": decision,
        "note": DECISION_ADVISORY_NOTE,
    }
    for name in UNSUPPORTED_NULL_FIELDS:
        outputs[name] = None
    return outputs


def _failed_model_outputs():
    outputs = {"status": STATUS_FAILED, "decision": None, "note": FAILED_NOTE}
    for name in RAW_OUTPUT_FIELDS:
        outputs[name] = None
    outputs["safe_credit_limit_rub_thousands"] = None
    for name in UNSUPPORTED_NULL_FIELDS:
        outputs[name] = None
    return outputs


def build_response(request, scorer):
    """Полный ответ ``POST /v1/predict``.

    ``scorer`` — вызываемый ``metrics -> dict`` с пятью сырыми прогнозами.
    Ошибки контракта запроса поднимают ``ValueError`` (422). Любая ошибка самого
    расчёта (включая недопустимый вывод модели и переполнение) даёт
    ``status: failed`` без утечки текста исключения и без ложного успеха.
    """
    application_id, document_version, is_mock, metrics = validate_request(request)
    try:
        model_outputs = assemble_model_outputs(scorer(metrics))
    except Exception:
        model_outputs = _failed_model_outputs()
    return {
        "schema_version": SCHEMA_VERSION,
        "application_id": application_id,
        "document_version": document_version,
        "is_mock": is_mock,
        "model_outputs": model_outputs,
    }


def predict_metrics(metrics, *, preprocessor, models):
    """Считает пять сырых прогнозов, повторяя логику оригинального скрипта.

    ``numpy``, ``pandas`` и всё, что нужно ``preprocessor``/``models``, должны быть
    переданы вызывающим. Импорты тяжёлых зависимостей выполняются только здесь,
    внутри расчёта, — на импорте модуля их нет. ``joblib`` не используется: ни
    загрузка, ни десериализация артефактов здесь не выполняются.
    """
    import numpy as np
    import pandas as pd

    df_raw = pd.DataFrame([metrics])

    rev = float(df_raw["revenue"].values[0])
    assets = float(df_raw["total_assets"].values[0])
    st_liab = float(df_raw["short_term_liabilities"].values[0])
    curr_assets = float(df_raw["current_assets"].values[0])
    equity = float(df_raw["equity"].values[0])

    # Дорасчёт недостающих коэффициентов — как в score_company_dict оригинала.
    df_raw["current_ratio"] = curr_assets / st_liab if st_liab > 0 else np.nan
    df_raw["cash_ratio"] = float(df_raw["cash"].values[0]) / st_liab if st_liab > 0 else np.nan
    df_raw["receivables_to_revenue"] = float(df_raw["receivables"].values[0]) / rev if rev > 0 else np.nan
    df_raw["debt_to_assets"] = st_liab / assets if assets > 0 else np.nan
    df_raw["equity_to_assets"] = equity / assets if assets > 0 else np.nan
    df_raw["negative_equity"] = 1 if equity < 0 else 0

    for col in ("margin_current", "operating_margin", "gross_margin", "revenue_growth_1y", "loss_streak"):
        if col not in df_raw.columns:
            df_raw[col] = np.nan

    x_eval = preprocessor.transform(df_raw)

    return {
        "forecast_revenue_rub_thousands": float(np.exp(models["revenue"].predict(x_eval)[0])),
        "net_margin_percent": float(models["net_margin"].predict(x_eval)[0]) * 100.0,
        "operating_margin_percent": float(models["operating_margin"].predict(x_eval)[0]) * 100.0,
        "loss_probability_percent": float(models["prob_loss"].predict_proba(x_eval)[0, 1]) * 100.0,
        "liquidity_crisis_probability_percent": float(models["prob_liquidity_crisis"].predict_proba(x_eval)[0, 1]) * 100.0,
    }


def score_with_bundle(request, *, preprocessor, models):
    """Удобная обвязка: полный ответ по заранее загруженному бандлу модели.

    Загрузку бандла (доверенный путь и класс ``FinancialPreprocessor`` из
    ``__main__``) выполняет вызывающая сторона; адаптер артефакты не читает.
    """
    def scorer(metrics):
        return predict_metrics(metrics, preprocessor=preprocessor, models=models)

    return build_response(request, scorer)
