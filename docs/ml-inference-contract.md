# Контракт инференса годовых ML-моделей (узел)

Документ описывает, как узел извлекает годовые входы, спрашивает у ML-адаптера
сырые прогнозы и один раз кладёт готовый `model_outputs` в callback Django.
Код адаптера — `mlinstance/adapter.py`; обученный скрипт
`mlinstance/predict_application.py` и артефакт `.joblib` остаются как есть и не
используются как библиотека. **`POST /v1/predict` реализует только узел
инференса; на стороне Django такого endpoint нет.** Django не ходит в ML и не
принимает сырые прогнозы отдельным запросом: он получает уже готовый
объединённый callback с `model_outputs` внутри `result.financial_analysis`.
Адаптер обучаемого инференса не выполняет: он принимает готовые сырые прогнозы
от переданного `scorer`.

## Поток

```text
извлечение документов → readiness.annual_model = true
  → POST /v1/predict (узел) → model_outputs
  → callback Django (один раз)
```

Callback у задания неизменяем: повторная доставка **того же** канонического
телесного JSON идемпотентна, но **другое** тело поверх завершённой задачи даёт
`409` и не перезаписывается. Поэтому инференс нужно завершить **до** callback и
положить `model_outputs` в тот же `result.financial_analysis`, а не отправлять
вторым callback. Готовность `readiness.annual_model` означает наличие
согласованных входов, а не одобрение кредита.

## Запрос `POST /v1/predict`

`schema_version` — `1`. `application_id`/`document_version`/`is_mock` обязательны
(`is_mock` — строгий `bool`). `annual_model_input` повторяет одноимённый блок
схемы v2: `unit: RUB_THOUSANDS`, `currency: RUB`, `ready: true`,
`missing_fields: []`, 10 полей `metrics`, плюс необязательный конверт `sources`.
Конверт `sources` адаптер читает, но **не копирует** в ответ.

```json
{
  "schema_version": 1,
  "application_id": "app-1",
  "document_version": 3,
  "is_mock": true,
  "annual_model_input": {
    "metrics": {
      "revenue": 50000.0, "net_profit_current": 3000.0,
      "operating_profit_current": 3500.0, "total_assets": 40000.0,
      "equity": 15000.0, "current_assets": 25000.0,
      "short_term_liabilities": 18000.0, "cash": 2000.0,
      "receivables": 12000.0, "okved_group": "46.1"
    },
    "unit": "RUB_THOUSANDS", "currency": "RUB",
    "ready": true, "missing_fields": [],
    "sources": {"revenue": [{"locator": "стр. 2110", "is_mock": true}]}
  }
}
```

Валидация: девять числовых полей — конечные числа (без `bool`); `revenue`,
`total_assets`, `current_assets`, `short_term_liabilities`, `cash`,
`receivables` неотрицательны; прибыли и капитал могут быть отрицательными;
`okved_group` — строка, начинающаяся с двух цифр. Нарушение — `ValueError`, узел
отвечает `422`. `ready != true` или непустые `missing_fields` — тоже `422`:
`scorer` при этом не вызывается.

## Ответ

Числа ниже — **синтетические** демонстрационные значения, не результат реального
обучения.

```json
{
  "schema_version": 1,
  "application_id": "app-1",
  "document_version": 3,
  "is_mock": true,
  "model_outputs": {
    "status": "completed",
    "forecast_revenue_rub_thousands": 60000.0,
    "safe_credit_limit_rub_thousands": 1000.0,
    "net_margin_percent": 5.0,
    "operating_margin_percent": 6.5,
    "loss_probability_percent": 10.0,
    "liquidity_crisis_probability_percent": 20.0,
    "default_probability_percent": null,
    "approval_probability_percent": null,
    "risk_grade": null,
    "decision": "ОДОБРЕНО (рекомендация модели)",
    "operating_cash_flow_forecast_rub": null,
    "note": "Рекомендация модели для сотрудника, а не решение банка..."
  }
}
```

`scorer` обязан вернуть пять сырых прогнозов: `forecast_revenue_rub_thousands`,
`net_margin_percent`, `operating_margin_percent`, `loss_probability_percent`,
`liquidity_crisis_probability_percent`. Адаптер сам считает
`safe_credit_limit_rub_thousands` = `0.20 × выручка / 12` (при низкой марже
`< 1 %` — ещё и `× 0.5`, как в оригинальном скрипте) и текст `decision`.
`loss_*`/`liquidity_*` — вероятности строго `0..100 %`; маржи — любые конечные.
Реализованы **две** модели вероятности — убыток и дефицит ликвидности. PD и
вероятность одобрения остаются `null`: таких моделей нет, их место не заполняем.

## Единицы и отображение

`*_rub_thousands` — тысячи рублей; `*_percent`/probabilities — проценты `0..100`;
`net_margin_percent`/`operating_margin_percent` — проценты, могут быть
отрицательными. `decision` — **рекомендация модели**, не решение банка;
`is_mock: true` — пометка демо-данных, тоже не решение.

## Статусы

`status` в блоке `model_outputs` схемы v2 объявлен как
`enum ["not_run", "completed", "failed"]` (раньше — `const "not_run"`).

* `completed` — расчёт прошёл, поля заполнены. Только этот статус Django
  показывает как выполненный. Нули — легитимные значения; вероятности обязаны
  быть конечными в `0..100 %`, денежные суммы — неотрицательными и конечными,
  маржи — конечными. Неподдержанные поля (`default_probability_percent`,
  `approval_probability_percent`, `risk_grade`,
  `operating_cash_flow_forecast_rub`) остаются `null` — их не подменяем
  выдуманными числами;
* `failed` — расчёт не выполнен (в том числе переполнение `exp` или недопустимый
  вывод модели). Числовые поля `null`, текст исключения наружу не выходит, ложного
  успеха нет. Django показывает фиксированное техническое сообщение, а не сырой
  `note` узла;
* `not_run` — значение по умолчанию в самой схеме v2, пока узел инференс не звал.

Django разбирает статус по строгому белому списку
(`node_integration/financial.py::_model_outputs`): `completed` → показ
корректных конечных значений, `failed` → отдельное сообщение без числовых полей,
любой другой/отсутствующий/неизвестный статус → «не запускалась». Потребители:
`node_integration/financial.py`, `node_integration/presentation.py`,
демо/фикстуры, шаблоны карточки банка.

## Зависимости и безопасность

Оригинальный скрипт на импорте сразу делает
`joblib.load('models/credit_risk_suite_full.joblib')` — то есть импорт/`import`
его как модуля выполняет десериализацию. Поэтому адаптер его не импортирует.
Путь в скрипте относительный `models/...`, а фактический файл лежит в
`mlinstance/credit_risk_suite_full.joblib` — при запуске из другого каталога
падает `FileNotFoundError`. Десериализация `joblib` — исполнение кода: загружать
можно только доверенный артефакт и в контролируемом окружении; класс
`FinancialPreprocessor` при загрузке требует, чтобы его имя было доступно в
`__main__` (или соответствующем модуле), иначе бандл не распакуется.

`mlinstance/adapter.py` на импорте тянет только стандартную библиотеку; `numpy`,
`pandas`, `scikit-learn` импортируются лениво внутри `predict_metrics`, а
`joblib` не используется вовсе. Точные версии `numpy`, `pandas`,
`scikit-learn`, `joblib`, которыми обучался бандл, неизвестны: их стоит
закрепить (pin) в окружении узла после проверки, что на них бандл даёт ожидаемые
прогнозы.

## Как запускать

Бандл загружает и доверяет **пользователь заранее**; адаптер ничего не грузит и
не исполняет. Готовый объект бандла передаётся как есть:

```python
from mlinstance.adapter import score_with_bundle

# bundle уже доверенно загружен пользователем вне адаптера (не здесь и не через адаптер)
response = score_with_bundle(
    request,
    preprocessor=bundle["preprocessor"],
    models=bundle["models"],
)
```

`request` — тело запроса `POST /v1/predict` выше. Ниже — оркестратор узла:
сначала готовый финансовый результат, затем проверка готовности, вызов сервиса
инференса и присвоение `model_outputs` **до** единственного callback. Псевдокод
намеренно не вводит новых зависимостей (`httpx` здесь только как пример; годится
`requests` или stdlib `urllib`).

```python
def prepare_and_infer(financial, bundle):
    annual = financial["annual_model_input"]
    if not (financial["readiness"]["annual_model"] and annual["ready"]):
        return financial  # входов нет — model_outputs оставляем как есть (not_run)

    request = {
        "schema_version": 1,
        "application_id": financial["application"]["application_id"],
        "document_version": financial["application"]["document_version"],
        "is_mock": financial["is_mock"],
        "annual_model_input": annual,
    }
    # localhost-сервис узла; X-API-Key между сервисами настоятельно рекомендуется.
    resp = httpx.post(ML_URL + "/v1/predict", json=request,
                      headers={"X-API-Key": ML_API_KEY}, timeout=30)
    resp.raise_for_status()
    answer = resp.json()

    # Ответ обязан относиться к той же заявке/версии/источнику данных.
    same = (answer["application_id"] == request["application_id"]
            and answer["document_version"] == request["document_version"]
            and answer["is_mock"] is request["is_mock"])
    if same and answer["model_outputs"]["status"] in ("completed", "failed"):
        financial["model_outputs"] = answer["model_outputs"]
    return financial
```

## Callback в Django

`model_outputs` уже лежит внутри существующего `financial_analysis`; общий JSON
финансового результата **не заменяется** частичным объектом. Каркас тела
callback (значения `financial` берутся из готового объекта целиком):

```json
{
  "job_id": "0123456789abcdef0123456789abcdef",
  "status": "completed",
  "verification_status": "not_verified",
  "files": [],
  "result": {
    "status": "complete",
    "schema_version": 2,
    "financial_analysis": "<существующий объект целиком, с model_outputs внутри>"
  }
}
```

`job_id` — 32 символа нижнего hex, тот же, что `external_job_id` попытки; это
связывает callback с конкретной попыткой отправки. Авторизация callback — Bearer
`CALLBACK_TOKEN` (отдельно от `X-API-Key` между сервисами). Повтор с тем же
каноническим телом идемпотентен, другое тело — `409`.

Ссылка: [финансовый API v2](financial-api-v2.md).

## Необязательный блок `model_audit`

Новый узел может добавлять в `financial_analysis` необязательный блок `model_audit` — это
провенанс (версии рантайма, ключи и хеш модели, допущения по признакам, пороги политики),
а не решение банка. Банковский интерфейс его не отображает и сырые значения не раскрывает.

- ключ необязателен и обратно совместим: результат без `model_audit` остаётся валидным
  (в корневую схему `model_audit` не добавлен в `required`);
- структура определена в `docs/schemas/financial-result-v2.schema.json` (`ModelAudit` и
  вложенные `ModelAuditFeatureAssumption`, `ModelAuditModel`, `ModelAuditRuntime`,
  `ModelAuditModelOutputs`, `ModelAuditRecommendation`, `ModelAuditThresholds`,
  `ModelAuditWarning`) с `additionalProperties: false`;
- неизвестные поля в `model_audit` или во вложенных объектах отклоняются — при расширении
  набора полей на стороне узла схему нужно обновить отдельно;
- `model_audit.model_outputs.status` объявлен свободной ограниченной строкой (не
  перечислением), так как полный набор статусов узла заранее не гарантирован.
