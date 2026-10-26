"""Черновые подсказки для подтверждения выписок.

Модуль только читает уже загруженные staff-документы и/или прошлый callback.
Он не вызывает сеть, LLM и не создаёт ``FinancialInputs``. Любая подсказка
остаётся черновиком: ``complete=False``, остатки не выводятся из оборотов.
"""

from __future__ import annotations

import calendar
import csv
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

MAX_CSV_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000
MAX_ACCOUNTS = 20
MAX_STATEMENTS = 5
MIN_YEAR = 2000
MAX_YEAR = 2100
MONTH_KEY_PATTERN = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")

COLUMNS = {
    "transaction_id",
    "account_id",
    "date",
    "direction",
    "amount",
    "currency",
    "category",
    "counterparty_id",
}
IN_CATEGORIES = {
    "customer_receipt",
    "internal_transfer",
    "loan_received",
    "other_inflow",
}
OUT_CATEGORIES = {
    "operating_payment",
    "debt_principal",
    "debt_interest",
    "debt_fee",
    "internal_transfer",
    "capital_expenditure",
    "other_outflow",
}
ACCOUNT_PATTERN = re.compile(r"^[\w.\-/]{1,64}$")


def _valid_month_key(value):
    if not isinstance(value, str) or MONTH_KEY_PATTERN.fullmatch(value) is None:
        return False
    return MIN_YEAR <= int(value[:4]) <= MAX_YEAR


def _empty():
    return {
        "rows": [],
        "accounts": [],
        "account_count": 0,
        "month_count": 0,
        "filenames": [],
        "is_draft": True,
        "rows_truncated": False,
        "accounts_truncated": False,
    }


def _month_bounds(dates, months):
    """Границы подсказки: календарные месяцы, а не выдуманные даты операций."""
    if dates:
        first, last = min(dates), max(dates)
        start = date(first.year, first.month, 1)
        end = date(last.year, last.month, calendar.monthrange(last.year, last.month)[1])
        return start, end, first, last
    first_month, last_month = min(months), max(months)
    start = date(int(first_month[:4]), int(first_month[5:7]), 1)
    last_year, last_month_number = int(last_month[:4]), int(last_month[5:7])
    end = date(last_year, last_month_number, calendar.monthrange(last_year, last_month_number)[1])
    return start, end, None, None


def _row(filename, account, dates, months, *, source):
    start, end, observed_start, observed_end = _month_bounds(dates, months)
    return {
        "filename": filename,
        "account_id": account,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "complete": False,
        "suggested": True,
        "source": source,
        "observed_start": observed_start.isoformat() if observed_start else None,
        "observed_end": observed_end.isoformat() if observed_end else None,
        "month_count": len(months),
        "_month_keys": sorted(months),
    }


def _parse_row(row):
    if set(row) != COLUMNS:
        return None
    account = (row.get("account_id") or "").strip()
    if not ACCOUNT_PATTERN.fullmatch(account):
        return None
    try:
        transaction_date = date.fromisoformat((row.get("date") or "").strip())
        amount = Decimal((row.get("amount") or "").strip())
    except (ValueError, InvalidOperation):
        return None
    if not MIN_YEAR <= transaction_date.year <= MAX_YEAR:
        return None
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        return None
    direction = row.get("direction")
    if direction == "in":
        categories = IN_CATEGORIES
    elif direction == "out":
        categories = OUT_CATEGORIES
    else:
        return None
    if row.get("currency") != "RUB" or row.get("category") not in categories:
        return None
    if not (row.get("transaction_id") or "").strip():
        return None
    return account, transaction_date


def _read_csv(document):
    if document.size <= 0 or document.size > MAX_CSV_BYTES:
        return None
    if Path(document.original_name).suffix.lower() != ".csv":
        return None
    try:
        with document.file.storage.open(document.file.name, "rb") as stream:
            raw = stream.read(MAX_CSV_BYTES + 1)
    except (OSError, ValueError, AttributeError):
        return None
    if len(raw) > MAX_CSV_BYTES:
        return None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    if not text.strip():
        return None
    first_line = text.splitlines()[0]
    delimiter = ";" if ";" in first_line else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if set(reader.fieldnames or ()) != COLUMNS:
        return None

    per_account = {}
    try:
        for index, row in enumerate(reader, 2):
            if index > MAX_ROWS + 1:
                return None
            parsed = _parse_row(row)
            if parsed is None:
                return None
            account, transaction_date = parsed
            entry = per_account.setdefault(account, {"dates": [], "months": set()})
            entry["dates"].append(transaction_date)
            entry["months"].add(transaction_date.strftime("%Y-%m"))
    except (csv.Error, UnicodeError):
        return None
    if per_account:
        return [
            _row(
                document.original_name,
                account,
                entry["dates"],
                entry["months"],
                source="csv_transactions",
            )
            for account, entry in sorted(per_account.items())
        ]
    return None


def _callback_rows(version, financial):
    """Fallback: уже полученные месяцы callback, только для приложенных файлов."""
    cash_flow = financial.get("cash_flow") if isinstance(financial, dict) else None
    months = cash_flow.get("months") if isinstance(cash_flow, dict) else None
    if not isinstance(months, list):
        return []
    attached = {
        document.original_name
        for document in version.documents.all()
        if Path(document.original_name).suffix.lower() == ".csv"
    }
    grouped = {}
    for month in months:
        if not isinstance(month, dict):
            continue
        month_key = month.get("month")
        accounts = month.get("accounts")
        sources = month.get("sources")
        if not _valid_month_key(month_key) or not isinstance(accounts, list):
            continue
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            filename = source.get("filename") or source.get("origin")
            if filename not in attached:
                continue
            for account in accounts:
                if not isinstance(account, str) or not ACCOUNT_PATTERN.fullmatch(account):
                    continue
                grouped.setdefault((filename, account), set()).add(month_key)
    rows = []
    for (filename, account), months_set in sorted(grouped.items()):
        rows.append(_row(filename, account, [], months_set, source="callback_months"))
    return rows


def build_statement_suggestions(version, *, financial=None, inputs=None):
    """Вернуть черновые строки для формы, не подтверждая их.

    ``inputs`` (подтверждённые ``FinancialInputs``) всегда главнее: при их
    наличии подсказки не строятся. Невалидный/слишком большой CSV просто
    пропускается, без исключений и без текста строк в ошибках.
    """
    if inputs is not None:
        return _empty()
    rows = []
    documents = list(version.documents.all().order_by("created_at", "pk"))
    for document in documents:
        parsed = _read_csv(document)
        if parsed:
            rows.extend(parsed)
    if not rows:
        rows = _callback_rows(version, financial)
    if not rows:
        return _empty()

    accounts = sorted({row["account_id"] for row in rows})
    filenames = sorted({row["filename"] for row in rows})
    all_months = set()
    for row in rows:
        # Считаем только фактически наблюдаемые месяцы, без заполнения пропусков.
        all_months.update(row.pop("_month_keys", ()))
    result = _empty()
    result.update(
        rows=rows[:MAX_STATEMENTS],
        accounts=accounts[:MAX_ACCOUNTS],
        account_count=len(accounts),
        month_count=len(all_months),
        filenames=filenames,
        rows_truncated=len(rows) > MAX_STATEMENTS,
        accounts_truncated=len(accounts) > MAX_ACCOUNTS,
    )
    return result
