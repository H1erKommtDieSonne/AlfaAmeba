# Внешний узел проверки документов (LAN)

Интеграция с внешним узлом проверки документов — **опциональная**. По умолчанию
она выключена: `DOCUMENT_NODE_ENABLED=false`, ключи пустые, а compose-профиль
`node-sender` не запускается. Локальные demo-воркеры `ml-worker-documents` и
`ml-worker-analytics` при этом работают как раньше.

Документ описывает демонстрационный LAN-стенд. Это не production-инструкция:
секреты не коммитятся, firewall и сеть узла настраиваются отдельно.

## Схема

```text
Django (192.168.1.103) ── POST /jobs + X-API-Key ──> внешний узел (192.168.1.105)
        ^                                                     │
        └──── POST callback + Bearer CALLBACK_TOKEN ──────────┘
```

- Django создаёт `NodeJob` в `PENDING` вместе с версией заявки.
- `send_node_jobs` забирает задачу, отправляет документы узлу и после ответа
  `202` переводит её в `WAITING`.
- Узел присылает результат на callback. Только callback переводит задачу в
  `COMPLETED`; sender результат не принимает.
- `DOCUMENT_NODE_API_KEY` — ключ Django → узел. `DOCUMENT_NODE_CALLBACK_TOKEN` —
  **отдельный** общий секрет узел → Django. Это не одно и то же значение.

## Запуск в Docker (opt-in профиль)

1. Скопируйте `.env.example` в `.env` и заполните значения. Файл `.env` не
   коммитится.
2. Сгенерируйте два разных длинных секрета, например:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

3. Укажите в `.env`:

   ```dotenv
   DOCUMENT_NODE_ENABLED=true
   DOCUMENT_NODE_URL=http://192.168.1.105:8000
   DOCUMENT_NODE_API_KEY=<ключ Django -> узел>
   DOCUMENT_NODE_CALLBACK_TOKEN=<общий секрет узел -> Django>
   DOCUMENT_NODE_TIMEOUT_SECONDS=30
   DOCUMENT_NODE_POLL_SECONDS=5
   ```

   `DOCUMENT_NODE_URL` — база адреса узла; sender добавляет `/jobs`.
   `DOCUMENT_NODE_API_KEY` и `DOCUMENT_NODE_CALLBACK_TOKEN` должны быть
   непустыми, иначе sender завершится ошибкой конфигурации. Пустой
   `CALLBACK_TOKEN` в Django означает, что callback отвечает `503`.

4. Запустите профиль:

   ```powershell
   docker compose --profile node-sender up --build -d
   docker compose logs -f node-sender
   ```

   Профиль добавляет только сервис `node-sender` с командой
   `python manage.py send_node_jobs --watch`. Он использует тот же образ Django,
   ту же БД `DATABASE_URL` и тот же том `media_data` (только чтение), ждёт
   `django` в состоянии `healthy` и подключён к сетям `web` и `internal`.
   `web` нужен для выхода в LAN, `internal` — для доступа к PostgreSQL. Без
   флага `--profile node-sender` сервис не создаётся и не влияет на остальные
   контейнеры.

5. Адрес callback для Docker: `http://192.168.1.103:8080/api/v1/document-node/callback/`
   (порт `APP_PORT`, по умолчанию `8080`, публикует nginx).

Остановить только отправитель:

```powershell
docker compose --profile node-sender stop node-sender
```

## Запуск без Docker (LAN-стенд)

Настройки `config.settings.lan` предназначены для демонстрации в закрытой сети:
`DEBUG=False`, явные `ALLOWED_HOSTS` (`localhost`, `127.0.0.1`,
`192.168.1.103`), CSRF-origin с портом `8000`, по умолчанию локальная SQLite
`credit_scoring.db` (или `DATABASE_URL`), обязательный `DJANGO_SECRET_KEY`.

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings.lan"
$env:DJANGO_SECRET_KEY = "<длинный случайный секрет>"
# При необходимости задайте DOCUMENT_NODE_* так же, как для Docker.

python manage.py migrate
python manage.py runserver 0.0.0.0:8000 --insecure --noreload
```

`--insecure` нужен только потому, что `runserver` при `DEBUG=False` иначе не
раздаёт статику. `runserver` не является production-сервером: не публикуйте его
наружу, для Docker используйте `config.settings.prod` и Gunicorn/nginx.

Адрес callback без Docker: `http://192.168.1.103:8000/api/v1/document-node/callback/`.

## Настройки

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `DOCUMENT_NODE_ENABLED` | `false` | Разрешает работу sender. Callback в Django отдельно зависит от токена. |
| `DOCUMENT_NODE_URL` | `http://192.168.1.105:8000` | База URL узла; отправка идёт на `<URL>/jobs`. |
| `DOCUMENT_NODE_API_KEY` | пусто | Ключ в заголовке `X-API-Key` при отправке. Для sender обязателен. |
| `DOCUMENT_NODE_CALLBACK_TOKEN` | пусто | Bearer-секрет callback. Пусто — callback отвечает `503`. |
| `DOCUMENT_NODE_TIMEOUT_SECONDS` | `30` | Таймаут HTTP-запроса к узлу, секунды. |
| `DOCUMENT_NODE_POLL_SECONDS` | `5` | Пауза `--watch` при пустой очереди, секунды. |

## Отправка задачи узлу

Sender отправляет `POST <DOCUMENT_NODE_URL>/jobs` с `multipart/form-data`:
поле `system_prompt`, затем необязательное `application_context` и от одного до
пяти файлов в поле `files`. Заголовок авторизации — `X-API-Key:
<DOCUMENT_NODE_API_KEY>`. Прокси-переменные окружения и автоматические
редиректы не используются.

`application_context` — строка с JSON-объектом (UTF-8, не больше 64 KiB).
Отправлять его не обязательно: старые запросы без поля продолжают работать.
Sender собирает контекст из неизменяемого снимка версии и не передаёт
персональные данные (ни организацию, ни ИНН, ни назначение):

- `schema_version` — `1`;
- `application_id`, `version_id`, `version_number` — идентификаторы версии;
- `product_code` — код продукта из снимка, если он корректен, иначе код FK
  версии; метки текущего каталога не используются;
- `collateral` — строго `true`/`false` из `snapshot.needs.collateral`. Если
  значение отсутствует или не является булевым, отправляется `null`, а не
  угаданное `false`;
- `checklist` — массив требований `{code, name, reason}` из снимка без статусов
  загрузки; если снимка нет, список берётся из правил продукта, и только когда
  код продукта известен;
- `checklist_source` — источник списка: `snapshot`, `fallback` или `empty`.

Если контекст не сериализуется в строгий UTF-8 JSON (без `NaN` и surrogate)
или превышает 64 KiB, задача помечается `FAILED` локально, без сетевого
запроса.

Успешный ответ узла — строго `202` и JSON:

```json
{
  "job_id": "0123456789abcdef0123456789abcdef",
  "status": "queued",
  "files_count": 2
}
```

`job_id` — ровно 32 символа в нижнем регистре hex. `files_count` должен
совпадать с числом отправленных файлов. После этого Django сохраняет
`external_job_id` и переводит задачу в `WAITING`.

Ошибки отправки:

- `4xx` от узла — задача `FAILED` с текстом ошибки; автоматического повтора нет;
- таймаут, обрыв соединения, `5xx`, редирект или некорректный `202` — задача
  `UNCERTAIN`: узел мог принять задачу, поэтому повторная отправка не делается;
- «зависшая» `SENDING` старше `TIMEOUT_SECONDS + 60` также переводится в
  `UNCERTAIN`, а не в `PENDING`.

## Callback узла

```http
POST /api/v1/document-node/callback/ HTTP/1.1
Host: 192.168.1.103:8000
Authorization: Bearer <DOCUMENT_NODE_CALLBACK_TOKEN>
Content-Type: application/json
X-Job-ID: 0123456789abcdef0123456789abcdef
Idempotency-Key: 0123456789abcdef0123456789abcdef
```

```json
{
  "job_id": "0123456789abcdef0123456789abcdef",
  "status": "completed",
  "result": {
    "summary": "результат проверки"
  },
  "files": [
    {"name": "statement.pdf", "status": "checked"}
  ],
  "verification_status": "needs_review",
  "model": "document-node-1"
}
```

Обязательные поля:

- `job_id` — 32 hex-символа в нижнем регистре, совпадает с `external_job_id`;
- `status` — только `completed`;
- `result` — JSON-объект (сохраняется как есть);
- `files` — JSON-массив (элементы сохраняются как есть, схема элементов не
  проверяется);
- `verification_status` — только `needs_review` или `not_verified`.

Необязательный `model` — строка. Если переданы `X-Job-ID` или
`Idempotency-Key`, их значение должно совпадать с `job_id`, иначе `400`.
`Authorization` обязателен и сравнивается с `CALLBACK_TOKEN`; `API_KEY` здесь
не подходит. Тело ограничено 2 MiB, `Content-Type` — `application/json`.

Ответы:

| Код | Значение |
| --- | --- |
| `200` | Результат принят; повтор того же канонического JSON идемпотентен и тоже даёт `200`. |
| `400` | Некорректное тело, поля, заголовки или размер `job_id`. |
| `401` | Нет или неверный Bearer-токен. |
| `404` | Django ещё не знает такой `job_id`. Узел повторяет доставку с backoff. |
| `409` | Задача уже завершена другим результатом или ещё не принимает callback (`PENDING`/`FAILED`). Повтор с тем же телом не поможет. |
| `413` | Тело больше 2 MiB. |
| `415` | `Content-Type` не `application/json`. |
| `503` | `DOCUMENT_NODE_CALLBACK_TOKEN` не задан. |

`404` возможен и при гонке: узел принял задачу и сразу отправил callback, а
Django ещё не успел сохранить `external_job_id`. Поэтому узел должен повторять
`404` с backoff. `409` означает расхождение результата или недопустимое
состояние — его нужно разбирать вручную, а не повторять бесконечно.

## Представление результата (`result.status`)

Внешний `status: completed` означает только доставленный callback. Содержательный
итог находится во вложенном `result.status`:

| `result.status` | Что это значит |
| --- | --- |
| `complete` | Проверка комплектности завершена. Не подтверждение достоверности и не одобрение банка. |
| `needs_documents` | Комплект неполный, нужно уточнение состава. |
| `needs_review` | Неопределённости или противоречия, нужна проверка сотрудником. |
| `processing_error` | Техническая ошибка обработки; к документам клиента не относится. |

Интерфейс показывает статус через безопасный помощник
`node_integration.presentation.node_review` по белому списку. Он возвращает
`{status, label, tone, message}` либо `None`. Сырые `summary` модели и текст
исключения наружу не выводятся: для `processing_error` используется общий
текст «Техническая ошибка проверки. Это не замечание к вашим документам;
требуется проверка сотрудником.», для остальных — фиксированные формулировки.

- Это представление доступно и банку, и клиенту (карточки текущей версии).
  Сырой `NodeJob.result` и JSON-панель остаются только в карточке банка.
- `complete` не означает `verified` и не является решением банка.
- Из `result` callback замечания **не извлекаются**: они приходят отдельным
  `/feedback/` и не дублируются.
- `NodeJob.status == COMPLETED` меняется только доставкой callback:
  `processing_error` **не** переводит задачу в `FAILED` и не запускает повторную
  отправку. Статус задания не мутирует.
- Старые callback без вложенного `result` и неизвестные статусы дают `None` и
  отображаются как раньше. Миграции не требуются.

## Замечания узла (feedback)

Если узлу не хватает полей в документах или разбор получился некорректным, он
может сообщить об этом структурированно. Django **не извлекает** замечания из
произвольного `result` callback: узел должен явно вызвать отдельный endpoint.
Замечания ничего не решают автоматически — статус заявки, версии и кредитные
решения не меняются, банк видит их и при необходимости сам запрашивает
доработку.

```http
POST /api/v1/document-node/feedback/ HTTP/1.1
Host: 192.168.1.103:8000
Authorization: Bearer <DOCUMENT_NODE_CALLBACK_TOKEN>
Content-Type: application/json
```

```json
{
  "feedback_id": "3f2c1a4e-7b6d-4c2a-9e10-2f5b8c1d4a66",
  "job_id": "0123456789abcdef0123456789abcdef",
  "issues": [
    {
      "code": "missing_field",
      "message": "Не найден номер расчётного счёта",
      "field": "account_number",
      "document_id": "9b1d...",
      "filename": "statement.pdf",
      "suggestion": "Загрузите полную выписку за период"
    }
  ]
}
```

- `feedback_id` — UUID, ключ идемпотентности: повтор с тем же телом даёт `200`,
  другое тело с тем же `feedback_id` — `409`.
- `job_id` — 32 hex-символа, как в callback. Версия и документы берутся из
  задачи узла; `document_id` обязан принадлежать этой версии, иначе `400`.
  `filename` — только для показа и никогда не адресует файл.
- `issues` — от 1 до 50 объектов. Обязательны `code` (нижний snake, ≤64) и
  `message` (непустой, ≤1000). Необязательные `field` ≤120, `document_id`,
  `filename` ≤255, `suggestion` ≤1000.
- Тело ≤256 KiB, `Content-Type: application/json`; дубли ключей, `NaN` и
  невалидный UTF-8 отклоняются. Ответ `200`: `feedback_id`, `job_id`,
  `issues_count`.

| Код | Значение |
| --- | --- |
| `200` | Замечания приняты; повтор того же тела идемпотентен. |
| `400` | Нарушен контракт тела или `document_id` не из этой версии. |
| `401` | Нет или неверный Bearer-токен. |
| `404` | Django ещё не знает такой `job_id`; узел повторяет с backoff. |
| `409` | Задача `PENDING`/`FAILED`, либо `feedback_id` с другим телом. |
| `413` / `415` / `503` | Тело >256 KiB / не `application/json` / токен не задан. |

## Ограничения и совместимость

Sender проверяет полный набор документов версии:

- от 1 до 5 файлов;
- каждый файл непустой и не больше 10 MiB;
- расширения: `.pdf`, `.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff`, `.docx`,
  `.xlsx`, `.pptx`, `.txt`, `.csv`, `.json`, `.md`;
- `.xls` **не поддерживается**;
- размер и SHA-256 каждого файла должны совпадать с сохранёнными в Django.

Форма банка принимает более широкий набор (включая `.xls`) и допускает файлы
до 20 MiB. Поэтому исходная заявка может быть принята банком, но sender
поместит `NodeJob` в `FAILED` из-за `.xls`, более 5 документов или файла
больше 10 MiB. Это ожидаемое ограничение узла: исправлять состав нужно новой
версией/доработкой заявки, а не повторной отправкой той же задачи.

## Статусы и ручное разбирательство

```text
PENDING -> SENDING -> WAITING -> COMPLETED
                    \-> FAILED
                    \-> UNCERTAIN
```

- `WAITING` может оставаться сколь угодно долго: если узел принял задачу, но
  не прислал callback (в том числе из-за собственной ошибки обработки),
  автоматического таймаута и повторной отправки нет. Проверяйте очередь узла
  вручную.
- `UNCERTAIN` означает, что результат отправки неизвестен (таймаут, обрыв,
  `5xx`, некорректный ответ). **Повторной отправки нет** — иначе можно создать
  дубль. Сверьте задачу на узле вручную и согласуйте результат.
- `FAILED` означает, что узел явно отклонил запрос (`4xx`) или документы не
  прошли локальную проверку. Текст причины — в `last_error`.
- `COMPLETED` меняется только callback; повторный callback с тем же JSON
  идемпотентен, с другим — `409`.

## Nginx и сеть

Nginx остаётся единственной публичной точкой входа. Внутренние pull-API
`/api/v1/llm/` и `/api/v1/analytics/` намеренно закрыты (`404`) и доступны
только контейнерам через `django:8000`. Callback
`/api/v1/document-node/callback/` через nginx доступен, потому что его вызывает
внешний узел; защита — Bearer-токен, а не сессия.

Сервис `node-sender` подключён к `web` (выход в LAN) и `internal` (PostgreSQL).
Сеть `internal` имеет `internal: true` и сама выхода наружу не даёт. Разрешение
исходящих соединений к `192.168.1.105` на хосте/firewall в репозиторий не
входит и настраивается отдельно.

## Диагностика

- Sender пишет «Отправка узлу не настроена» — проверьте `DOCUMENT_NODE_ENABLED`,
  непустые `API_KEY` и `CALLBACK_TOKEN`, корректный `URL`.
- Callback `503` — пустой `CALLBACK_TOKEN` в Django.
- Callback `401` — узел прислал `API_KEY` вместо `CALLBACK_TOKEN` или секреты
  не совпадают.
- Callback `404` — неверный `job_id`, задача ещё не в `WAITING`, либо гонка
  после `202`; узел должен повторить.
- Callback `409` — уже сохранён другой результат; не перезаписывайте его
  повтором, разбирайтесь вручную.
- `FAILED` из-за документов — проверьте число файлов, размер, расширение и
  SHA-256; `.xls` нужно заменить на поддерживаемый формат.

## Развёртывание обновления (замечания узла)

Миграция `node_integration.0002_nodefeedback` — аддитивная (только новая
таблица), данные заявок не меняются. Порядок без Docker:

```cmd
set "DJANGO_SETTINGS_MODULE=config.settings.lan"
.venv\Scripts\python.exe manage.py migrate --noinput
```

После миграции перезапустите Django и sender (они не подхватывают изменения на
лету). `runserver --insecure` раздаёт статику напрямую, поэтому `collectstatic`
не нужен; для nginx/Gunicorn выполните `collectstatic` как обычно. Перед
перезапуском убедитесь, что нет задачи в `SENDING`: она не переотправляется
автоматически. Новый endpoint начинает отвечать `401` без токена, а не `404`.

## Быстрый запуск на Windows (cmd), без Docker

Из корня репозитория `D:\Alfa_credit_scoring`; виртуальное окружение `.venv`
уже содержит зависимости. Оба процесса обязаны использовать один и тот же
`DJANGO_SETTINGS_MODULE=config.settings.lan`:

```cmd
set "DJANGO_SETTINGS_MODULE=config.settings.lan"

start "alfa-django" /b .venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000 --insecure --noreload
start "alfa-sender" /b .venv\Scripts\python.exe manage.py send_node_jobs --watch
```

Проверка и остановка:

```cmd
tasklist /FI "IMAGENAME eq python.exe"
taskkill /PID <pid> /F
```

Узел вызывает callback `http://192.168.1.103:8000/api/v1/document-node/callback/`.
Это демонстрационный LAN-стенд (`DEBUG=False` и `runserver`), не production.
