"""Детерминированные demo-заглушки извлечения и аналитики.

Никакой модели, OCR или случайности здесь нет. Факты в payload — явно
помеченные синтетические fixture-данные, а не результат разбора файла.
"""

import hashlib
import re

from .queue_client import DocumentVerificationError, PermanentClientError

VALID_SCENARIOS = ("normal", "missing_data", "manual_review")
DOCUMENT_TYPE = "demo_document"
SCHEMA_VERSION = "1.0"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_RESULT_KEYS = {
    "schema_version",
    "is_demo",
    "model_version",
    "policy_version",
    "scenario_id",
    "recommendation",
    "summary",
    "reason_codes",
    "components",
}


def _synthetic_fixture(scenario):
    """Fixture facts. Все значения вымышлены и не извлечены из документа."""
    if scenario == "normal":
        return {
            "document_kind": "synthetic_bank_statement",
            "synthetic": True,
            "applicant_name": "СИНТЕТИЧЕСКИЙ КЛИЕНТ",
            "period": "2026-01..2026-06",
            "currency": "RUB",
            "monthly_income": "120000.00",
            "monthly_obligations": "25000.00",
            "transactions": [
                {"date": "2026-01-15", "amount": "120000.00", "synthetic": True},
                {"date": "2026-02-15", "amount": "118000.00", "synthetic": True},
            ],
        }
    if scenario == "manual_review":
        return {
            "document_kind": "synthetic_manual_review_fixture",
            "synthetic": True,
            "applicant_name": "СИНТЕТИЧЕСКИЙ КЛИЕНТ",
            "period": "2026-01..2026-06",
            "currency": "RUB",
            "monthly_income": "90000.00",
            "monthly_obligations": "80000.00",
            "manual_review_flag": True,
            "manual_review_reason": "Синтетический fixture требует ручной проверки",
        }
    return {
        "document_kind": "synthetic_missing_data_fixture",
        "synthetic": True,
        "applicant_name": None,
        "period": None,
        "currency": None,
        "monthly_income": None,
        "monthly_obligations": None,
        "missing_fields": ["period", "monthly_income", "monthly_obligations"],
    }


def verify_document(document, content):
    """Проверить реально скачанные байты против size/sha256 из claim."""
    if not isinstance(document, dict):
        raise DocumentVerificationError("Описание документа некорректно")
    if not document.get("document_id"):
        raise DocumentVerificationError("В документе нет document_id")
    if not isinstance(content, (bytes, bytearray)):
        raise DocumentVerificationError("Тело документа не является байтами")
    expected_size = document.get("size")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int):
        raise DocumentVerificationError("size документа отсутствует или не число")
    if len(content) != expected_size:
        raise DocumentVerificationError(
            f"Размер документа не совпал: ожидался {expected_size}, получен {len(content)}"
        )
    expected_sha = document.get("sha256")
    if not isinstance(expected_sha, str) or not _SHA256_RE.match(expected_sha):
        raise DocumentVerificationError("sha256 документа отсутствует или некорректен")
    actual_sha = hashlib.sha256(bytes(content)).hexdigest()
    if actual_sha.lower() != expected_sha.lower():
        raise DocumentVerificationError("SHA-256 документа не совпал")
    return len(content), actual_sha.lower()


def build_document_result(document, content, scenario):
    """Сериализованный результат stub-извлечения одного документа."""
    if scenario not in VALID_SCENARIOS:
        raise PermanentClientError("Недопустимый DEMO_SCENARIO")
    byte_count, actual_sha = verify_document(document, content)
    payload = {
        "is_demo": True,
        "scenario_id": scenario,
        "extraction_method": "stub",
        "ocr_performed": False,
        "synthetic_fixture": True,
        "fixture_note": (
            "Payload содержит только синтетические fixture-факты. "
            "Он не извлечён из содержимого документа."
        ),
        "verified_download": {
            "byte_count": byte_count,
            "sha256": actual_sha,
        },
        "fixture": _synthetic_fixture(scenario),
    }
    return {
        "document_id": str(document["document_id"]),
        "document_type": DOCUMENT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "confidence": None,
        "payload": payload,
    }


def extraction_summary(document_results):
    return {
        "document_count": len(document_results),
        "is_demo": True,
        "extraction_method": "stub",
        "ocr_performed": False,
    }


def _scenario_for(claim):
    """Сценарий и качество evidence; любое сомнение ведёт в missing_data."""
    documents = claim.get("documents")
    if not isinstance(documents, list) or not documents:
        return "missing_data", "UNKNOWN", "empty_documents"

    scenarios = set()
    for document in documents:
        if not isinstance(document, dict):
            return "missing_data", "UNKNOWN", "malformed_document"
        payload = document.get("payload")
        if not isinstance(payload, dict) or not payload:
            return "missing_data", "UNKNOWN", "empty_payload"
        if (
            payload.get("is_demo") is not True
            or payload.get("synthetic_fixture") is not True
            or payload.get("extraction_method") != "stub"
        ):
            return "missing_data", "UNKNOWN", "unsupported_payload"
        scenario = payload.get("scenario_id")
        if scenario not in VALID_SCENARIOS:
            return "missing_data", "UNKNOWN", "unsupported_scenario"
        fixture = payload.get("fixture")
        if scenario == "normal" and (
            not isinstance(fixture, dict)
            or fixture.get("synthetic") is not True
            or fixture.get("monthly_income") in (None, "")
            or fixture.get("monthly_obligations") in (None, "")
        ):
            return "missing_data", "UNKNOWN", "incomplete_normal_fixture"
        scenarios.add(scenario)

    if len(scenarios) != 1:
        return "missing_data", "UNKNOWN", "inconsistent_scenarios"
    scenario = scenarios.pop()
    if claim.get("extraction_status") != "COMPLETED":
        status = "UNKNOWN" if claim.get("extraction_status") == "FAILED" else "INCOMPLETE"
        return "missing_data", status, "extraction_not_completed"
    return scenario, "OK", "ok"


def _component(status, summary):
    return {"status": status, "summary": summary}


def build_analysis_result(claim):
    """Строгий demo-результат под analytics.services.validate_result."""
    model_version = claim.get("model_version")
    policy_version = claim.get("policy_version")
    if not isinstance(model_version, str) or not model_version:
        raise PermanentClientError("В claim нет model_version")
    if not isinstance(policy_version, str) or not policy_version:
        raise PermanentClientError("В claim нет policy_version")

    scenario, evidence_status, _reason = _scenario_for(claim)
    if scenario == "normal":
        result = {
            "schema_version": "1.0",
            "is_demo": True,
            "model_version": model_version,
            "policy_version": policy_version,
            "scenario_id": "normal",
            "recommendation": "PROCEED_TO_REVIEW",
            "summary": (
                "Симулированный демонстрационный результат: все четыре компонента "
                "условно в норме. Это не кредитное решение, без PD и без "
                "автоматического одобрения."
            ),
            "reason_codes": ["DEMO_ONLY", "SYNTHETIC_FIXTURE", "NO_REAL_MODEL"],
            "components": {
                "evidence": _component(
                    "OK", "Демо: синтетические документы проверены, OCR не выполнялся"
                ),
                "radar": _component("OK", "Симулированные демо-показатели"),
                "cashflow": _component("OK", "Симулированный денежный поток"),
                "scoring": _component(
                    "OK", "Демо-скоринг: реальная модель и PD не использовались"
                ),
            },
        }
    elif scenario == "manual_review":
        result = {
            "schema_version": "1.0",
            "is_demo": True,
            "model_version": model_version,
            "policy_version": policy_version,
            "scenario_id": "manual_review",
            "recommendation": "MANUAL_REVIEW",
            "summary": (
                "Демонстрационный сценарий ручной проверки: синтетический fixture "
                "просит участия специалиста. Автоматического решения нет."
            ),
            "reason_codes": [
                "DEMO_ONLY",
                "SYNTHETIC_FIXTURE",
                "MANUAL_REVIEW_REQUIRED",
            ],
            "components": {
                "evidence": _component(
                    "OK", "Демо: синтетические документы проверены, OCR не выполнялся"
                ),
                "radar": _component(
                    "MANUAL_REVIEW", "Синтетический radar-сигнал требует ручной проверки"
                ),
                "cashflow": _component("OK", "Симулированный денежный поток"),
                "scoring": _component(
                    "OK", "Демо-скоринг: реальная модель и PD не использовались"
                ),
            },
        }
    else:
        evidence = "UNKNOWN" if evidence_status == "UNKNOWN" else "INCOMPLETE"
        result = {
            "schema_version": "1.0",
            "is_demo": True,
            "model_version": model_version,
            "policy_version": policy_version,
            "scenario_id": "missing_data",
            "recommendation": "REQUEST_INFORMATION",
            "summary": (
                "Демо-результат неполный: извлечение отсутствует, пустое или "
                "неподдерживаемое. Нужны дополнительные данные; уверенного "
                "нормального вывода нет."
            ),
            "reason_codes": [
                "DEMO_ONLY",
                "MISSING_DATA",
                "REQUEST_INFORMATION",
            ],
            "components": {
                "evidence": _component(
                    evidence,
                    "Демо: документы не извлечены или payload не поддержан",
                ),
                "radar": _component("UNKNOWN", "Нет данных для demo-radar"),
                "cashflow": _component("UNKNOWN", "Нет данных для demo-cashflow"),
                "scoring": _component("UNKNOWN", "Нет данных для demo-scoring"),
            },
        }

    if set(result) != _RESULT_KEYS:  # защита от случайного расширения схемы
        raise PermanentClientError("Внутренняя ошибка схемы demo-результата")
    return result
