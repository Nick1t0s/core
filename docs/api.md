## HTTP API

Контракт запросов между скрапперами, core, агентами, балансировщиком и кабинетом core. Имена операций в путях — HTTP-эндпоинты, а не вызовы функций между процессами.

### Общие правила

- Пути начинаются от адреса соответствующего сервера. Для очередей это `task_endpoint` из реестра core.
- Тела запросов и ответов — JSON, кроме загрузки исходных файлов и скачивания данных задач. JSON-запросы передаются с `Content-Type: application/json`.
- Все даты — ISO 8601 со смещением UTC. ID объектов дерева — UUID v7.
- `owner` определяется сервером по сессии, `scraper_id` или `agent_id`; клиент не может выбрать другого владельца.
- Числа `trust_level` — неотрицательные целые. `limit` — положительное целое; пределы поиска и чтения публикуются в `GET /api/search/schema`.
- Массивы эмбеддингов в примерах сокращены. Для Qwen3-VL-Embedding-2B передаются все 2048 чисел.
- Списки с пагинацией возвращают `items` и `next_cursor`; `null` означает конец выдачи. Для списков кабинета по умолчанию `limit = 20`, максимум 100. Следующая страница запрашивается с теми же фильтрами и полученным `cursor`.
- Ошибка возвращается в виде `{"error":{"code":"invalid_request","message":"Описание ошибки"}}`. `401` — нет действующего секрета или сессии, `403` — операция не разрешена, `404` — объект отсутствует или недоступен, `413` — превышен размер файла, `422` — неверные данные. Ошибки не содержат секретов, SQL и путей файловой системы.

### Скраппер → core

Каждый запрос содержит заголовки:

```http
X-Scraper-ID: <scraper_id>
Authorization: Bearer <секрет скраппера>
```

#### `GET /api/limits`

Без параметров. Ответ `200`:

```json
{"max_file_size":16106127360}
```

#### `GET /api/records/status`

Обязательные query-параметры: `source_id` и `source_url`. Значение `source_url` кодируется как URL-параметр.

```http
GET /api/records/status?source_id=youtube&source_url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabc
```

#### `GET /api/records/{record_id}/status`

Альтернатива проверке по ссылке: обязательный `record_id` в пути, других параметров нет.

Оба эндпоинта отвечают `200`:

```json
{"record_id":"<UUID>","status":"processing"}
```

`status`: `unknown`, `processing`, `done`, `error`. Внешний `processing` объединяет внутренние `parsing` и `processing`. Если записи нет у владельца скраппера, ответ `{"record_id":null,"status":"unknown"}`; наличие чужой копии ничего не меняет.

#### `POST /api/records`

`Content-Type: multipart/form-data; boundary=...`. Первая часть `record` имеет тип `application/json`:

```json
{
  "source_id": "youtube",
  "source_url": "https://www.youtube.com/watch?v=abc",
  "source_created_at": "2026-10-31T12:00:00+03:00",
  "scraped_at": "2026-11-01T10:00:00+03:00",
  "title": "Название видео",
  "author": "Название канала",
  "parent_source_url": null,
  "metadata": {"duration": 120, "shorts": false},
  "trust_level": 2,
  "text": "Название и описание видео",
  "files": [{"name": "video.mp4", "mime": "video/mp4", "size": 12345678}]
}
```

Обязательны `source_id`, `source_url`, `scraped_at`, `trust_level`. Необязательны `source_created_at`, `title`, `author`, `parent_source_url`, `metadata`, `text`, `files`. Для отсутствующих `text` и `files` используются пустая строка и пустой массив; у записи должен быть непустой текст или хотя бы один файл. Для каждого файла обязательны `name`, `mime`, `size` (неотрицательное целое число байт). Неизвестная дата публикации — `null` или отсутствие поля. `source_id` должен совпадать с регистрацией скраппера.

За `record` идут части с именем `files`: бинарные данные в порядке массива `record.files`. Core проверяет метаданные, дубликат и заявленные размеры до чтения файловых частей; фактическое число частей и размеры также должны совпадать с описанием. `owner`, `record_id`, `file_id` выдаёт core, скраппер их не задаёт.

После сохранения всех файлов — `202 Accepted`:

```json
{
  "record_id": "<UUID>",
  "status": "processing",
  "files": [{"file_id": "<UUID>", "name": "video.mp4"}]
}
```

В ответе `files` сохраняет порядок загрузки. Разбор выполняется после ответа. Если запись уже есть у того же владельца, core не читает файловые части и отвечает `200`:

```json
{"record_id":"<UUID>","exists":true,"status":"done"}
```

Здесь `status` — текущий внешний статус записи. Повторная отправка ничего не меняет, включая `trust_level`. Пустая запись — `422`, превышение `max_file_size` — `413`. Прерванная или некорректная загрузка не оставляет зарегистрированной записи и её файлов.

### Балансировщик → core: реестр

#### `GET /api/task-sources`

Без параметров. Заголовок:

```http
Authorization: Bearer <секрет балансировщика для core>
```

Ответ `200`:

```json
{
  "version": 42,
  "items": [
    {
      "task_source_id": "<ID источника>",
      "task_endpoint": "https://agent.example",
      "priority": 2,
      "enabled": true,
      "draining": false,
      "token": "<секрет для API очередей этого источника>"
    }
  ]
}
```

`items` содержит core и зарегистрированных агентов, включая отключаемые источники, которым ещё нужно вернуть взятые задачи. `task_endpoint` — базовый адрес сервера без пути API очередей. `token` принимается только очередями соответствующего `task_source_id`, не даёт доступа к поиску и кабинету, не является секретным `agent_id`. Для очередей core также используется отдельный `token`: секрет запроса реестра не отправляется другим серверам. `balancer_id` проверяется по регистрации, связанной с `token`.

Все агенты равноприоритетны и выше core. Точные правила обновления списка и отключения источников — Core, 11.6.

### Балансировщик → core или агент: очереди

Следующие пути добавляются к `task_endpoint`. `task_source_id` обязателен в пути каждого запроса. Заголовок:

```http
Authorization: Bearer <token этого источника>
```

`agent_id` для очередей не используется. Один сервер может обслуживать несколько `task_source_id`, но задачи и результаты между ними не смешиваются.

#### `GET /api/task-sources/{task_source_id}/check_tasks`

Без дополнительных параметров. Ответ `200`:

```json
{"transcribe":true,"ocr":false,"embed":true}
```

Значения показывают наличие задач в `wait`. Вызов не забирает задачи.

#### `POST /api/task-sources/{task_source_id}/claim`

Все поля обязательны:

```json
{"type":"transcribe","limit":8,"balancer_id":"<balancer_id>"}
```

`type`: `transcribe`, `ocr`, `embed`. Ответ `200` для задачи с файлом:

```json
{
  "tasks": [
    {
      "task_id": "<UUID>",
      "type": "transcribe",
      "input": {
        "url": "/api/task-sources/<ID>/tasks/<UUID>/input",
        "mime": "audio/flac"
      }
    }
  ]
}
```

Для текстового эмбеддинга:

```json
{
  "tasks": [
    {
      "task_id": "<UUID>",
      "type": "embed",
      "input": {"text": "Текст для получения эмбеддинга"}
    }
  ]
}
```

У `transcribe` вход — аудио через `input.url`, у `ocr` — изображение через `input.url`, у `embed` — `input.text` либо изображение через `input.url`. `input.mime` задаётся для файла. `input.url` — путь на том же сервере; балансировщик скачивает данные с тем же `token` перед запуском воркера. Произвольные внешние URL не используются. Пустая очередь — `{"tasks":[]}`.

Задачи атомарно переходят из `wait` в `processing` до ответа; отдельного запроса о начале выполнения нет. Данные уже подготовлены источником: core читает файлы из `task_storage`, текст из БД. Правила обработки отсутствующего файла и транзакций — Core, 4.2.

#### `GET /api/task-sources/{task_source_id}/tasks/{task_id}/input`

Оба ID обязательны в пути, других параметров нет. Ответ `200` — бинарные данные с `Content-Type`, указанным в `input.mime`. Файл доступен только для задачи выбранного источника в `processing`, взятой балансировщиком, которому принадлежит `token`. Если задача уже удалена или возвращена в очередь, ответ `404`; балансировщик не запускает её по старой выдаче.

#### `POST /api/task-sources/{task_source_id}/result`

Обязательны `balancer_id`, массив `results`, `task_id` и `payload` каждого результата. Для транскрибации и OCR:

```json
{
  "balancer_id": "<balancer_id>",
  "results": [{"task_id": "<UUID>", "payload": {"text": "Полученный текст"}}]
}
```

Пустой `text` допустим и не считается ошибкой. Для эмбеддинга:

```json
{
  "balancer_id": "<balancer_id>",
  "results": [
    {
      "task_id": "<UUID>",
      "payload": {"embed_model": "Qwen3-VL-Embedding-2B", "embedding": [0.12, -0.34, 0.56]}
    }
  ]
}
```

`text` либо `embedding` и `embed_model` обязательны согласно типу задачи. Ответ `204 No Content` после коммита всех результатов; каждый результат и его цикл готовности фиксируются своей транзакцией. Core записывает `payload.embedding` в `chunk.embed`. Результаты удалённых задач и задач не в `processing` игнорируются. Результаты существующей задачи другого балансировщика не принимаются.

#### `POST /api/task-sources/{task_source_id}/fail`

```json
{
  "balancer_id": "<balancer_id>",
  "task_ids": ["<UUID>"],
  "error": {
    "message": "CUDA out of memory",
    "attempts": 2,
    "worker": "transcribe",
    "model": "large-v3-turbo"
  }
}
```

Обязательны `balancer_id`, непустой `task_ids`, `error.message`, положительное целое `error.attempts`. `error.worker` и `error.model` необязательны. Ответ `204 No Content` после фиксации ошибок. Ошибка запускает цикл очистки, а не возвращает задачу в `wait`. Правила проверки задачи те же, что у `/result`.

#### `POST /api/task-sources/{task_source_id}/reset`

Обязательное поле:

```json
{"balancer_id":"<balancer_id>"}
```

Ответ `204 No Content`. Все задачи этого балансировщика в `processing` переходят в `wait`, `taken_at` и `taken_by` очищаются. Завершённые задачи не меняются. Запрос используется при запуске балансировщика, вытеснении и отключении источника; доступен также для `draining`-источника.

### Агент → core: поиск, чтение, удаление

Каждый запрос содержит:

```http
Authorization: Bearer <agent_id>
```

Core определяет по `agent_id` регистрацию, `owner` и текущий `trust_level`. Для поиска и чтения обязательны совпадение владельца, `record.trust_level < agent.trust_level` и `record.status = done`. Для удаления статус записи может быть любым.

#### `GET /api/search/schema`

Необязательные query-параметры `target` и `source_id`. Без них возвращается вся схема. Например:

```http
GET /api/search/schema?target=chunk&source_id=telegram
```

Ответ `200`: версия схемы, типы объектов, поля, связи, операции фильтрации и ограничения. `target`: `record`, `file`, `fragment`, `element`, `text`, `chunk`.

#### `POST /api/search`

```json
{
  "target": "chunk",
  "where": {
    "all": [
      {"scope": "record", "path": ["source_id"], "op": "eq", "value": "telegram"},
      {"scope": "record", "path": ["source_created_at"], "op": "gte", "value": "2026-10-01T00:00:00+03:00"}
    ]
  },
  "select": [
    {"scope": "self", "path": ["text"]},
    {"scope": "record", "path": ["title"]}
  ],
  "order_by": [{"scope": "record", "path": ["source_created_at"], "direction": "desc"}],
  "limit": 20,
  "cursor": null
}
```

| Параметр | Обязателен | Значение |
|---|---|---|
| `target` | Да | Тип возвращаемого объекта из списка выше |
| `where` | Нет | Дерево условий; без него дополнительных фильтров нет |
| `select` | Нет | Поля объекта и предков; без него возвращаются ID и сведения об источнике |
| `query` | Нет | Объект с обязательными `embedding` и `embed_model` для семантического поиска |
| `order_by` | Нет | Список полей с `scope`, `path`, `direction` (`asc` или `desc`); для fragment-предка также точная `depth` |
| `limit` | Нет | По умолчанию 20, верхний предел публикуется в схеме |
| `cursor` | Нет | Непрозрачный курсор продолжения или `null` |

Для семантического поиска добавляется поле:

```json
{"query":{"embed_model":"Qwen3-VL-Embedding-2B","embedding":[0.12,-0.34,0.56]}}
```

Агент предварительно получает вектор через собственную очередь и балансировщик. Строка с текстом запроса вместо объекта `query` не принимается. Без `query` эмбеддинг не нужен. На следующих страницах сохраняются параметры исходного поиска и передаётся полученный `cursor`.

Ответ `200`:

```json
{
  "schema_version": 1,
  "items": [
    {
      "type": "chunk",
      "id": "<UUID>",
      "record_id": "<UUID>",
      "text": "Найденный текст",
      "source_id": "telegram",
      "source_url": "https://t.me/channel/123",
      "title": "Название записи"
    }
  ],
  "next_cursor": null,
  "truncated": false
}
```

Каждый результат включает ID и происхождение объекта, запрошенные поля, доступные ID предков и позицию в файле. При семантическом поиске добавляются `score` и `matches` — ограниченный список подтверждающих chunk'ов с ID, текстом при его наличии и позицией. Подробные правила `where`, `select`, обхода предков, сортировки, ранжирования и курсоров описаны в спецификации агента.

#### `POST /api/read`

```json
{"target":"record","id":"<UUID>","mode":"text","limit":20,"cursor":null}
```

Обязательны `target`, `id`, `mode`. Типы `target` те же, что у поиска. `mode`: `metadata`, `children`, `text`. Необязательны `select` (для `metadata`, как у поиска), `limit` (по умолчанию 20), `cursor`. На следующих страницах передаются те же `target`, `id`, `mode`, `select` и полученный курсор.

Ответ `200`:

```json
{"schema_version":1,"items":[],"next_cursor":null,"truncated":false}
```

`metadata` возвращает объект с полями и путём предков, `children` — непосредственных детей, `text` — сохранённые текстовые chunk'и объекта или его поддерева. Массив `items` содержит объекты выбранного режима. Для текстовых chunk'ов сохраняются ID, границы, вид текста и известная позиция в исходнике. Медиа заново не извлекаются, служебные task не возвращаются. Отсутствующий и недоступный объект дают одинаковый `404`.

#### `DELETE /api/records/{record_id}`

Обязательный `record_id` в пути, тела нет. Ответ `204 No Content` после удаления. Отсутствующий и недоступный объект дают одинаковый `404`. Проверка владельца и уровня и удаление выполняются одной транзакцией. Копии других владельцев сохраняются, поздние результаты удалённых задач игнорируются.

### Кабинет → core

После входа браузер передаёт cookie `session`. В изменяющих запросах под сессией также передаётся `X-CSRF-Token` из ответа входа. Для входа и регистрации сессия не нужна; они доступны только с допустимого origin интерфейса core. Поля `owner`, роли и чужие ID регистраций не позволяют расширить права пользователя.

| Эндпоинт | Параметры запроса | Успешный ответ |
|---|---|---|
| `POST /api/auth/register` | JSON: обязательные `login`, `password`; `invite_code` обязателен в режиме `invite` | `201`, `{"id":"<ID пользователя>","login":"<логин>"}`; затем отдельный вход |
| `POST /api/auth/login` | JSON: обязательные `login`, `password` | `200`, cookie `session` и `{"csrf_token":"<токен>"}` |
| `POST /api/auth/logout` | Без тела | `204`, сессия отозвана, cookie удалена |
| `GET /api/agents` | Query: необязательные `limit`, `cursor` | `200`, `items`, `next_cursor` |
| `POST /api/agents` | JSON: обязательные `mode`, `trust_level`, `task_endpoint` | `201`, регистрация и новый `agent_id` |
| `PATCH /api/agents/{id}` | JSON: хотя бы одно из `trust_level`, `task_endpoint`, `enabled` | `200`, обновлённая регистрация |
| `POST /api/agents/{id}/rotate-key` | Без тела | `200`, `{"agent_id":"<новый ключ>"}`; старый отозван |
| `DELETE /api/agents/{id}` | Без тела | `204`, регистрация отключена; завершение удаления после возврата задач |
| `GET /api/scrapers` | Query: необязательные `limit`, `cursor` | `200`, `items`, `next_cursor` |
| `POST /api/scrapers` | JSON: обязательный `source_id` (`youtube` или `telegram`) | `201`, регистрация и `secret` |
| `PATCH /api/scrapers/{scraper_id}` | JSON: обязательный `enabled` | `200`, обновлённая регистрация |
| `DELETE /api/scrapers/{scraper_id}` | Без тела | `204`, скраппер удалён, его записи сохраняются |
| `GET /api/records` | Query: необязательные `status`, `source_id`, `limit`, `cursor` | `200`, `items`, `next_cursor` |
| `GET /api/records/{record_id}` | Без дополнительных параметров | `200`, поля record и список file с их статусами, `error`, `error_detail` |
| `PATCH /api/records/{record_id}` | JSON: обязательный `trust_level` | `200`, обновлённая запись |
| `DELETE /api/records/{record_id}` | Без тела | `204` |

Здесь `{id}` агента — внутренний ID регистрации, не секретный `agent_id`. `mode`: `web_ai`, `web_manual`, `telegram_ai`, `telegram_manual` — отдельная регистрация для каждого используемого режима. Новые регистрации включены (`enabled = true`).

Обычный ответ с регистрацией агента содержит `id`, `task_source_id`, `owner`, `mode`, `trust_level`, `task_endpoint`, `enabled`; `task_source_id` совпадает с внутренним `id`. `agent_id` добавляется только при создании, а новый ключ возвращается при перевыпуске. Регистрация скраппера содержит `scraper_id`, `owner`, `source_id`, `enabled`; `secret` добавляется только при создании. Секреты не возвращаются в списках.

`GET /api/records` в кабинете принимает внутренние статусы `parsing`, `processing`, `done`, `error` и показывает только собственные записи. Эндпоинты изменения чужих регистраций и записей дают тот же `404`, что отсутствующие объекты. Отдельные file не удаляются. Сессионные операции кабинета не доступны по `agent_id` или секрету скраппера; исключение — явно разрешённое агенту удаление записи по правилам выше.

### Административный раздел → core

Cookie `session` должна принадлежать администратору. Для изменяющих запросов обязателен `X-CSRF-Token`.

| Эндпоинт | Параметры запроса | Успешный ответ |
|---|---|---|
| `GET /api/admin/users` | Query: необязательные `limit`, `cursor` | `200`, `items`, `next_cursor` |
| `POST /api/admin/users` | JSON: обязательные `login`, `password` | `201`, `id`, `login`, `blocked = false` |
| `PATCH /api/admin/users/{user_id}` | JSON: обязательный `blocked` | `200`, `id`, `login`, `blocked` |
| `POST /api/admin/users/{user_id}/reset-password` | JSON: обязательный `password` | `204` |
| `GET /api/admin/settings` | Без параметров | `200`, `{"registration_mode":"closed"}` |
| `PATCH /api/admin/settings` | JSON: обязательный `registration_mode` (`closed`, `open`, `invite`) | `200`, обновлённая настройка |
| `POST /api/admin/invites` | Без тела | `201`, `{"id":"<ID>","code":"<одноразовый код>"}` |
| `DELETE /api/admin/invites/{id}` | Без тела | `204`, приглашение отозвано |
| `GET /api/admin/records` | Query: необязательные `owner`, `status`, `source_id`, `limit`, `cursor` | `200`, `items`, `next_cursor` |
| `GET /api/admin/records/{record_id}` | Без дополнительных параметров | `200`, запись и file со статусами и ошибками |
| `DELETE /api/admin/records/{record_id}` | Без тела | `204` |

Список пользователей содержит `id`, `login`, `blocked`, без паролей и хешей. Статусы и формат записей те же, что у кабинета, но администратору доступны все владельцы. Создание агентов и скрапперов остаётся в пользовательском кабинете.

### Воркеры и внешние сервисы

У воркеров HTTP-эндпоинтов нет. Балансировщик скачивает входные данные задач, запускает нужный воркер процессом, передаёт пачку и получает результаты всей пачки. Способ обмена между процессами ещё не закреплён. Результаты источнику отправляет балансировщик через HTTP `/result`, ошибки — через `/fail`.

Telegram-скраппер читает историю через MTProto. Конкретный способ доступа к YouTube и провайдер LLM ещё не выбраны, поэтому их внешние эндпоинты здесь не задаются. Локальные сайты агентов и скрапперов настраивает владелец; этот контракт закрепляет их обращения к core и API очередей, а не устройство их собственных интерфейсов.
