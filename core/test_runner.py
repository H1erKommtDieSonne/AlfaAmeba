import tempfile

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings


class MediaIsolatedRunner(DiscoverRunner):
    """Тесты пишут загруженные документы во временный каталог, а не в media/ проекта.

    Подмена на весь прогон, а не в отдельных классах: иначе новый тест с загрузкой
    однажды забудет её сделать и молча насорит в рабочий каталог.
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._media = tempfile.TemporaryDirectory()
        self._override = override_settings(MEDIA_ROOT=self._media.name)
        self._override.enable()

    def teardown_test_environment(self, **kwargs):
        super().teardown_test_environment(**kwargs)
        self._override.disable()
        self._media.cleanup()
