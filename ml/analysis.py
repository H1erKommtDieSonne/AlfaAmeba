"""Explainable document checks. Never makes lending or acceptance decisions."""

import re
from decimal import Decimal, InvalidOperation

VERSION = "document-checks-1"
EXPECTED = {"statement": "bank_statement", "estimate": "estimate", "contract": "contract",
            "debt": "credit_documents", "collateral": "ownership"}
TYPES = {"bank_statement": "Выписка по счёту", "estimate": "Смета / предложение",
         "contract": "Контракт", "credit_documents": "Кредитные документы",
         "ownership": "Документы на имущество", "unknown": "Тип не определён"}
PATTERNS = {
    "bank_statement": r"выписка\s+(?:по|из)\s+(?:расч[её]тно[мг][оу]\s+)?сч[её]т|выписка\s+из\s+лицевого\s+сч[её]та",
    "estimate": r"\bсмета\b|коммерческое\s+предложение",
    "credit_documents": r"кредитный\s+договор|график\s+погашения\s+кредита|справка\s+об\s+остатке\s+(?:основного\s+)?долга",
    "contract": r"(?:государственный|муниципальный)\s+контракт|договор\s+(?:поставки|подряда)|контракт\s+на\s+",
    "ownership": r"выписка\s+из\s+егрн|свидетельство\s+о\s+(?:государственной\s+регистрации\s+)?праве\s+собственности",
}


def evidence(chunk, match):
    return {"location": chunk["location"], "text": chunk["text"][max(0, match.start()-40):match.end()+80][:260]}


def review_result(document_id, code, message):
    return {"document_id": document_id, "document_type": "unknown", "schema_version": VERSION,
            "confidence": None, "payload": {"status": "NEEDS_REVIEW", "type_label": TYPES["unknown"],
            "findings": [{"code": code, "message": message}], "fields": [], "evidence": []}}


def analyze(document, chunks, application):
    matches = {}
    for kind, pattern in PATTERNS.items():
        for chunk in chunks:
            match = re.search(pattern, chunk["text"], re.I)
            if match:
                matches[kind] = evidence(chunk, match)
                break
    kind = next(iter(matches)) if len(matches) == 1 else "unknown"
    findings, fields = [], []
    expected = EXPECTED.get(document.get("requirement_code"))
    if kind == "unknown":
        findings.append({"code": "TYPE_UNCERTAIN", "message": "Тип не определён однозначно по тексту. Нужна ручная проверка."})
    elif expected and kind != expected:
        findings.append({"code": "TYPE_MISMATCH", "message": f"В этом пункте ожидается «{TYPES[expected]}», по тексту найдено «{TYPES[kind]}». Проверьте назначение файла.", **matches[kind]})
    # Generic INNs may belong to a supplier or bank: compare only explicitly labelled applicant INNs.
    for chunk in chunks:
        for match in re.finditer(r"ИНН\s+(?:заявителя|за[её]мщика|владельца\s+сч[её]та)\s*[:|]?\s*(\d{12}|\d{10})(?!\d)", chunk["text"], re.I):
            item = {"name": "ИНН заявителя", "value": match.group(1), **evidence(chunk, match)}
            fields.append(item)
            if application.get("inn") and match.group(1) != application["inn"]:
                findings.append({"code": "INN_MISMATCH", "message": "ИНН, подписанный в документе как ИНН заявителя, отличается от заявки.", **evidence(chunk, match)})
        if kind == "estimate":
            for match in re.finditer(r"(?:итого\s+по\s+смете|общая\s+стоимость\s+проекта)\s*[:|]?\s*([\d ][\d\s\u00a0]*(?:[,.]\d{1,2})?)\s*(?:₽|руб)", chunk["text"], re.I):
                try:
                    amount = Decimal(re.sub(r"\s", "", match.group(1)).replace(",", "."))
                    requested = Decimal(str(application.get("amount", "0")))
                except InvalidOperation:
                    continue
                fields.append({"name": "Стоимость проекта, ₽", "value": str(amount), **evidence(chunk, match)})
                if requested.is_finite() and amount != requested:
                    findings.append({"code": "AMOUNT_REVIEW", "message": "Стоимость проекта отличается от суммы заявки. Это может быть частичное финансирование; уточните долю собственных средств.", **evidence(chunk, match)})
    return {"document_id": document["document_id"], "document_type": kind, "schema_version": VERSION,
            "confidence": None, "payload": {"status": "NEEDS_REVIEW" if findings else "EXTRACTED",
            "type_label": TYPES[kind], "findings": findings[:20], "fields": fields[:20],
            "evidence": list(matches.values())[:5], "extractor": "local-text-v1"}}


def summarize(results, documents, application):
    by_id = {item["document_id"]: item for item in results}
    checklist = []
    for requirement in application.get("checklist", []):
        attached = [by_id[doc["document_id"]] for doc in documents if doc.get("requirement_code") == requirement["code"]]
        status = "MISSING" if not attached else "NEEDS_REVIEW" if any(item["payload"]["status"] != "EXTRACTED" for item in attached) else "EXTRACTED"
        checklist.append({"code": requirement["code"], "name": requirement["name"], "status": status})
    return {"analysis_version": VERSION, "document_count": len(results),
            "review_count": sum(item["payload"]["status"] == "NEEDS_REVIEW" for item in results),
            "checklist": checklist, "notice": "Автоматический разбор завершён. Принятие документов и решение по заявке остаются за сотрудником."}
