# Перенос и настройка Ollama Docs Node на Linux и Windows

**Назначение:** поднять на новом компьютере нашу ноду обработки документов: Ollama + Qwen 3.5 35B-A3B, FastAPI, Redis, worker, callback-worker и cleanup. Исходная Kali-нода при этом остаётся без изменений.

## 0. Что входит в комплект

Нужна папка `ollama-docs-portable`:

```text
ollama-docs-portable/
├── api/                     # FastAPI, обработка документов, воркеры
├── tests/
├── compose.yaml             # Ollama + API
├── compose.override.yaml    # Redis + воркеры + cleanup + volumes
├── compose.gpu.yaml         # Дополнительный доступ к NVIDIA GPU
├── .env.example             # Шаблон настроек без секретов
└── README.md
```

На исходной Kali проверить:

```bash
cd ~/ollama-docs-portable
ls -la compose.yaml compose.override.yaml compose.gpu.yaml .env.example
cat compose.gpu.yaml
grep -nE 'ollama_models|image: ollama-docs-api|runtime:|NODE_BIND_IP' compose.yaml
docker compose --env-file .env.example config --quiet && echo 'CONFIG OK'
```

Правильный универсальный вариант: в `compose.yaml` модель подключена как `ollama_models:/root/.ollama/models`, API имеет `image: ollama-docs-api`, отсутствует `runtime: nvidia`; в `compose.gpu.yaml`:

```yaml
services:
  ollama:
    gpus: all
```

Если эти условия не выполнены, сначала нужно завершить правки из этапа подготовки переносимой конфигурации; **не запускайте архив на новом ПК вслепую**.

Упаковать заново на Kali:

```bash
cd ~
python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
root = Path.home() / 'ollama-docs-portable'
archive = Path.home() / 'ollama-docs-portable-v2.zip'
with ZipFile(archive, 'w', ZIP_DEFLATED) as z:
    for p in root.rglob('*'):
        if not p.is_file():
            continue
        if any(x in {'.git', '__pycache__', '.venv'} for x in p.parts):
            continue
        if p.name in {'.env', '.env.local'} or p.suffix in {'.pyc', '.pem', '.key'}:
            continue
        z.write(p, Path(root.name) / p.relative_to(root))
with ZipFile(archive) as z:
    assert z.testzip() is None
    assert any(n.endswith('/compose.gpu.yaml') for n in z.namelist())
print('ARCHIVE OK:', archive)
PY
```

Перед передачей архива проверьте, что **секреты не попали также в код, тесты и README**. Архив не содержит модель, `.env`, историю Redis и документы из Docker volumes.

## 1. Требования к новой машине

- 64-битная Linux или Windows 10/11 с Docker и Docker Compose v2.
- Для GPU: совместимая NVIDIA и корректные драйверы. На Windows необходим backend **WSL2**.
- Достаточно RAM и свободного диска. `qwen3.5:35b-a3b` занимает около **22 ГБ** только для весов; оставьте дополнительное место под образы, кэш и документы.
- CPU-only запуск возможен, но на такой модели будет существенно медленнее.
- Новый компьютер должен иметь доступ к Django-серверу по локальной сети; Django должен иметь доступ к порту API новой ноды.

## 2А. Установка на Linux (Ubuntu / Debian / Kali)

### Подготовка Docker и NVIDIA

1. Установите Docker Engine и Compose v2 по инструкции именно для своего дистрибутива. Проверьте:

```bash
docker version
docker compose version
```

2. Если нужна NVIDIA GPU, установите драйвер и проверьте:

```bash
nvidia-smi
```

3. Установите **NVIDIA Container Toolkit** по официальной инструкции. После установки:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

На новом ПК это безопасная настройка; **не выполняйте перезапуск Docker на старой рабочей Kali без необходимости**.

Официальные руководства:

- Docker Engine для Ubuntu: https://docs.docker.com/engine/install/ubuntu/
- Docker Engine для Debian: https://docs.docker.com/engine/install/debian/
- Compose: https://docs.docker.com/compose/install/linux/
- NVIDIA Toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html

### Распаковка

Скопируйте `ollama-docs-portable-v2.zip` на новый Linux-компьютер, затем:

```bash
unzip ollama-docs-portable-v2.zip -d "$HOME"
cd "$HOME/ollama-docs-portable"
cp .env.example .env
nano .env
```

Настройки `.env` описаны в разделе 3.

## 2Б. Установка на Windows 10/11

1. Установите/обновите драйвер NVIDIA **для Windows**.
2. В PowerShell **от имени администратора** включите WSL2 (если он ещё не установлен):

```powershell
wsl --install
wsl --update
```

Если установщик требует перезагрузку, перезагрузите Windows.

3. Установите [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/). В настройках Docker Desktop включите **Use WSL 2 based engine** и режим **Linux containers**.
4. Проверьте в PowerShell:

```powershell
docker version
docker compose version
wsl --status
```

5. Скопируйте ZIP в `Downloads` и распакуйте:

```powershell
Expand-Archive -Path "$HOME\Downloads\ollama-docs-portable-v2.zip" -DestinationPath "$HOME\Documents" -Force
Set-Location "$HOME\Documents\ollama-docs-portable"
Copy-Item .env.example .env
notepad .env
```

Не нужно устанавливать отдельную Ollama в Windows и не нужно ставить NVIDIA Container Toolkit **внутрь WSL2**, если Docker Desktop уже обеспечивает GPU-доступ.

Официальное руководство по GPU на Windows: https://docs.docker.com/desktop/features/gpu/

## 3. Настройка `.env` (одинаковая на Linux и Windows)

Заполните файл `.env` значениями для **нового** компьютера:

```dotenv
NODE_BIND_IP=0.0.0.0
API_KEY=PASTE_UNIQUE_RANDOM_API_KEY_HERE
CALLBACK_URL=http://192.168.1.103:8000/api/v1/document-node/callback/
CALLBACK_TOKEN=PASTE_DJANGO_CALLBACK_TOKEN_HERE
FEEDBACK_URL=http://192.168.1.103:8000/api/v1/document-node/feedback/
```

- `0.0.0.0` означает прослушивание всех сетевых интерфейсов хоста. Для доступа только с самого узла используйте `127.0.0.1`, но Django с другого ПК тогда не подключится.
- `API_KEY` — отдельный ключ для запросов **Django → нода**. Его значение должно совпадать с `DOCUMENT_NODE_API_KEY` в Django.
- `CALLBACK_TOKEN` — секрет **нода → Django**. Его значение должно совпадать с `DOCUMENT_NODE_CALLBACK_TOKEN` в Django. **Не используйте один секрет для обеих ролей.**
- `CALLBACK_URL` и `FEEDBACK_URL` указывают на существующий Django. Порт `8000` соответствует нашему LAN-стенду; при другом способе запуска Django адрес будет другим.
- IP в `NODE_BIND_IP` — это **адрес привязки**, а не URL для Django. Реальный IP нового узла потом указывается в `DOCUMENT_NODE_URL` на стороне Django.

Сгенерировать ключ можно локально:

Linux:
```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Windows PowerShell (если установлен Python):
```powershell
py -c "import secrets; print(secrets.token_urlsafe(48))"
```

Не присылайте значения секретов в чат и не архивируйте `.env`.

**Сетевой доступ:** открыть TCP 8000 на новом узле **только для IP Django** (`192.168.1.103` в нашем стенде); не открывать наружу Redis 6379 и Ollama 11434. На Windows — правило Windows Defender Firewall для входящих TCP 8000 с RemoteAddress Django; на Linux — аналогичное правило firewall.

## 4. Запуск всех контейнеров

Из папки `ollama-docs-portable` на **любой** системе:

### NVIDIA GPU

```bash
docker compose -f compose.yaml -f compose.override.yaml -f compose.gpu.yaml config --quiet
docker compose -f compose.yaml -f compose.override.yaml -f compose.gpu.yaml up -d --build
```

### Без NVIDIA GPU (CPU-only)

```bash
docker compose -f compose.yaml -f compose.override.yaml config --quiet
docker compose -f compose.yaml -f compose.override.yaml up -d --build
```

Не пытайтесь использовать GPU-вариант, если устройство и GPU runtime не настроены. Если появляется ошибка об отсутствии `ollama-docs-api` — убедитесь, что в `compose.yaml` у сервиса `api` указан `image: ollama-docs-api` рядом с `build: ./api`.

Ожидаемые контейнеры:

```text
ollama-docs
docs-api
docs-redis
docs-worker
docs-callback-worker
docs-cleanup
```

Проверить:

```bash
docker ps
docker logs --tail=80 docs-api
docker logs --tail=80 docs-worker
```

Имена контейнеров фиксированные, поэтому одновременно запускать исходный и переносимый проект **на одном Docker-хосте** нельзя без переименования контейнеров. На разных компьютерах конфликтов нет.

## 5. Загрузка модели Qwen

Модель **не включена** в архив и в универсальном Compose хранится в постоянном Docker volume `ollama_models`.

Одинаково для Linux и PowerShell:

```bash
docker exec ollama-docs ollama pull qwen3.5:35b-a3b
docker exec ollama-docs ollama list
```

Объём загрузки — порядка 22 ГБ. Если на новой машине модель уже скачана отдельно, её можно перенести, но это другая процедура. Для первого запуска проще `ollama pull`.

Проверка работы модели:

```bash
docker exec ollama-docs ollama run qwen3.5:35b-a3b 'Ответь одним словом: готово'
```

При NVIDIA можно наблюдать загрузку видеокарты командой `nvidia-smi` на хосте; после запроса `docker exec ollama-docs ollama ps` показывает размещение загруженной модели. При 6 ГБ VRAM часть модели остаётся в RAM.

## 6. Проверка API

На новом **Linux**:

```bash
curl --noproxy '*' http://127.0.0.1:8000/health
```

На новом **Windows** (PowerShell):

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

С **Django-компьютера Windows** проверить сеть:

```powershell
curl.exe --noproxy "*" http://NEW_NODE_IP:8000/health
```

`NEW_NODE_IP` заменить на IP нового узла. Для получения перечня моделей используйте `/models`, если этот endpoint настроен и авторизован как в API.

Проверка вывода логов callback:

```bash
docker logs --tail=100 docs-callback-worker
```

## 7. Подключение Django

На компьютере с Django изменить настройки sender:

```dotenv
DOCUMENT_NODE_ENABLED=true
DOCUMENT_NODE_URL=http://NEW_NODE_IP:8000
DOCUMENT_NODE_API_KEY=THE_SAME_VALUE_AS_NODE_API_KEY
DOCUMENT_NODE_CALLBACK_TOKEN=THE_SAME_VALUE_AS_NODE_CALLBACK_TOKEN
```

После правок перезапустить Django и `node-sender` тем способом, которым они развёрнуты. Для LAN-стенда без Docker в проекте Django используется `python manage.py send_node_jobs --watch`.

**Не подключайте одновременно два узла к одному sender без специально реализованной маршрутизации:** в текущей конфигурации sender отправляет задачу на один `DOCUMENT_NODE_URL`.

Схема передачи: Django `POST /jobs` → Redis/worker/Qwen → callback Django; замечания — отдельный `POST /api/v1/document-node/feedback/`.

## 8. Что перенос *не* делает автоматически

- История прошлых заданий и исходные документы **не переносятся** — для этого нужна отдельная миграция `jobs_data`, а при необходимости Redis.
- Автоматическая отправка замечаний клиенту, интерфейс удаления/замены файла и повторное создание `NodeJob` после исправлений **ещё требуют доработки Django и обработки на ноде**. Одной установкой они не появятся.
- По текущему контракту sender принимает **1–5 файлов, до 10 MiB каждый**, и **не поддерживает `.xls`**, даже если форма банка позволяет больше.
- При неотработанной ошибке worker текущий Django `NodeJob` может остаться в `WAITING`: необходимы обработка ошибок, таймауты и сверка статусов.

## 9. Обслуживание и частые ошибки

| Симптом | Проверить |
|---|---|
| Не запускается `ollama` с GPU | Драйвер NVIDIA, WSL2 (Windows) или Toolkit (Linux), Docker GPU support |
| `pull` не работает | Интернет на новом узле и свободное место |
| Worker не видит модель | `docker exec ollama-docs ollama list`, затем логи `docs-worker` |
| `docs-api` не запустился | `docker logs docs-api`, корректность `.env` и сборки образа |
| Django не видит API | IP нового узла, порт 8000, firewall, `NODE_BIND_IP`, прокси |
| Callback 401 | Несовпадение `CALLBACK_TOKEN` с Django |
| Callback 404 | Django ещё не зарегистрировал внешний `job_id`; повтор с backoff |
| Callback 409 | Расхождение данных/состояния; не повторять бесконечно |
| Зависла проверка | Состояние worker, Redis, Ollama и Django `NodeJob` |

Контейнеры **остановить без удаления данных**:

```bash
docker compose -f compose.yaml -f compose.override.yaml down
```

Если запускался GPU-режим, можно использовать тот же набор `-f` с `compose.gpu.yaml` и для остановки. **Не используйте `down -v`**, он удаляет Docker volumes с моделями и задачами.

После перезагрузки машины Docker должен быть запущен; `restart: unless-stopped` помогает поднять контейнеры автоматически. На Windows Docker Desktop тоже должен быть запущен.

## Источники

- Docker Desktop Windows GPU: https://docs.docker.com/desktop/features/gpu/
- Docker Desktop Windows installation: https://docs.docker.com/desktop/setup/install/windows-install/
- NVIDIA Container Toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html
- Docker Compose GPU: https://docs.docker.com/compose/how-tos/gpu-support/
- Qwen 3.5 model library: https://ollama.com/library/qwen3.5/tags
