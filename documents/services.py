import hashlib

from .models import ApplicationDocument, DocumentProcessingJob


def save_documents_and_enqueue(version, documents, retained_documents=()):
    """Сохранить файлы версии заявки и поставить одну задачу в очередь LLM.

    Вызывать внутри транзакции отправки: версия, файлы и задача появляются вместе.
    """
    for entry in documents:
        requirement_code, uploaded_file = entry if isinstance(entry, tuple) else ("", entry)
        digest = hashlib.sha256()
        for chunk in uploaded_file.chunks():
            digest.update(chunk)
        uploaded_file.seek(0)
        ApplicationDocument.objects.create(
            version=version,
            file=uploaded_file,
            original_name=uploaded_file.name[:255],
            requirement_code=requirement_code,
            content_type=(uploaded_file.content_type or "application/octet-stream")[:120],
            size=uploaded_file.size,
            sha256=digest.hexdigest(),
        )
    for document in retained_documents:
        ApplicationDocument.objects.create(
            version=version, file=document.file.name, original_name=document.original_name,
            requirement_code=document.requirement_code, content_type=document.content_type,
            size=document.size, sha256=document.sha256,
        )
    return DocumentProcessingJob.objects.create(version=version)
