import hashlib

from .models import ApplicationDocument, DocumentProcessingJob


def save_documents_and_enqueue(version, documents):
    """Сохранить файлы версии заявки и поставить одну задачу в очередь LLM.

    Вызывать внутри транзакции отправки: версия, файлы и задача появляются вместе.
    """
    for uploaded_file in documents:
        digest = hashlib.sha256()
        for chunk in uploaded_file.chunks():
            digest.update(chunk)
        uploaded_file.seek(0)
        ApplicationDocument.objects.create(
            version=version,
            file=uploaded_file,
            original_name=uploaded_file.name[:255],
            content_type=(uploaded_file.content_type or "application/octet-stream")[:120],
            size=uploaded_file.size,
            sha256=digest.hexdigest(),
        )
    return DocumentProcessingJob.objects.create(version=version)
