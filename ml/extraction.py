"""Bounded text extraction. Scans and unsupported formats require human review."""

import csv
import io
from pathlib import Path
from zipfile import ZipFile

from defusedxml import ElementTree

MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 150_000
MAX_ROWS = 2000


class ExtractionIssue(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def extract(data, filename):
    if len(data) > MAX_BYTES:
        raise ExtractionIssue("LIMIT", "Файл превышает лимит обработки 20 МБ.")
    suffix = Path(filename).suffix.lower()
    chunks, text_size = [], 0

    def add(location, text):
        nonlocal text_size
        text = str(text or "").strip()
        if not text:
            return
        text_size += len(text)
        if text_size > MAX_TEXT:
            raise ExtractionIssue("LIMIT", "Документ слишком объёмный для автоматического разбора.")
        chunks.append({"location": location, "text": text})

    if suffix == ".pdf":
        from pypdf import PdfReader
        if not data.startswith(b"%PDF-"):
            raise ExtractionIssue("INVALID", "Содержимое не соответствует формату PDF.")
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractionIssue("ENCRYPTED", "PDF защищён паролем. Нужна незашифрованная копия.")
        if len(reader.pages) > 100:
            raise ExtractionIssue("LIMIT", "Более 100 страниц: нужна ручная проверка.")
        for index, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if not text.strip():
                # Do not silently ignore scanned pages in mixed PDFs.
                raise ExtractionIssue("OCR_REQUIRED", f"Страница {index} без текста: нужно распознавание или ручная проверка.")
            add(f"Страница {index}", text)
    elif suffix in {".docx", ".xlsx"}:
        with ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 5000 or sum(item.file_size for item in entries) > 50 * 1024 * 1024:
                raise ExtractionIssue("LIMIT", "Слишком большой распакованный документ.")
            if suffix == ".docx":
                root = ElementTree.fromstring(archive.read("word/document.xml"))
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                for index, paragraph in enumerate(root.findall(".//w:p", ns), 1):
                    add(f"Абзац {index}", "".join(paragraph.itertext()))
                if any(item.filename.startswith("word/media/") for item in entries):
                    raise ExtractionIssue("EMBEDDED_MEDIA", "В DOCX есть изображения: текст не покрывает весь документ, нужна ручная проверка.")
            else:
                from openpyxl import load_workbook
                workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
                try:
                    count = 0
                    for sheet in workbook:
                        if sheet.max_column and sheet.max_column > 100:
                            raise ExtractionIssue("LIMIT", "В таблице более 100 столбцов.")
                        for index, row in enumerate(sheet.iter_rows(), 1):
                            count += 1
                            if count > MAX_ROWS:
                                raise ExtractionIssue("LIMIT", "В таблице более 2000 строк.")
                            if any(cell.data_type == "f" for cell in row):
                                raise ExtractionIssue("FORMULAS", "В таблице есть формулы. Нужна копия со значениями или ручная проверка.")
                            add(f"Лист {sheet.title}, строка {index}", " | ".join(str(cell.value or "") for cell in row))
                finally:
                    workbook.close()
    elif suffix == ".csv":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1251")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            # Statements often mix metadata rows and a wider transaction table.
            # Prefer the delimiter in the first non-empty header over Sniffer's column consistency.
            header = next((line for line in text.splitlines() if line.strip()), "")
            delimiter = max((";", "\t", ","), key=header.count)
            dialect = None
        rows = csv.reader(io.StringIO(text), dialect) if dialect else csv.reader(io.StringIO(text), delimiter=delimiter)
        for index, row in enumerate(rows, 1):
            if index > MAX_ROWS or len(row) > 100:
                raise ExtractionIssue("LIMIT", "Таблица превышает лимит 2000 строк / 100 столбцов.")
            add(f"Строка {index}", " | ".join(row))
    elif suffix in {".png", ".jpg", ".jpeg"}:
        raise ExtractionIssue("OCR_REQUIRED", "Для изображения нужно распознавание или ручная проверка. OCR пока не подключён.")
    else:
        raise ExtractionIssue("UNSUPPORTED", "Формат пока не разбирается автоматически. Приложите PDF с текстом, DOCX, XLSX или CSV.")
    if not chunks:
        raise ExtractionIssue("EMPTY", "Не удалось извлечь текст. Нужна другая копия или ручная проверка.")
    return chunks
