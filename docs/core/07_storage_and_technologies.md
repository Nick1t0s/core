### 7. Хранение и технологии

Рекомендуется PostgreSQL с расширением pgvector версии не ниже 0.7 (нужен тип `halfvec`, см. ниже); СУБД пока не зафиксирована. Фрагменты SQL в этом разделе — эскизы для PostgreSQL.

- Типы `record_status`, `container_status`, `task_status`, `element_kind`, `task_type`, `text_kind` — enum. Enum — тип поля, которое может принимать только одно значение из заданного списка. В SQLite enum нет, там используется `TEXT` с проверкой `CHECK`.
- Каждый тип объектов дерева лежит в своей таблице: `record`, `file`, `fragment`, `text`, `element`, `chunk`, `task`. Внешние ключи с `ON DELETE CASCADE`: авторизованное удаление записи (см. 6 и 11.5) — это удаление строки record, остальное удаляется каскадом. Цикл очистки удаляет строки явно, снизу вверх (2.6); каскад для него — подстраховка.
- Исходные файлы лежат в долговременном хранилище core по пути `/storage/<source_id>/<record_id>/<file_id>.<исходное расширение>`; ни пути, ни хэша в БД нет. Данные для задач лежат во временном `task_storage` на файловой системе (не в БД; путь выводится из `record_id` и `element_id`) и удаляются очисткой хранилищ целым каталогом записи (3.3).
- Все поля с данными, специфичными для источника или типа файла (`metadata`, `meta`), имеют тип JSONB.
- Пользователи, настройки регистрации, приглашения и регистрации агентов/скрапперов хранятся в core, а не в параметрах клиента. Owner у record, agent и scraper — внешний ключ на app_user. Индекс record(owner, trust_level, id) поддерживает обязательный фильтр доступа; индексы agent(owner) и scraper(owner) — списки в кабинете. Между пользователями общих record и таблицы связи владельцев нет. Удаление пользователя не должно каскадно уничтожать его записи: вместо этого учётную запись блокируют; отдельный сценарий окончательного удаления данных не входит в этот контракт.
- ID компонентов постоянны; вместо хеширования agent_id хранится token_hash. Удаление скраппера логическое: token отзывается, строка сохраняется для record.scrapper, ID не переиспользуется. Core проверяет совпадение owner скраппера и записи и заполняет scrapper из авторизованной регистрации при приёме; клиент изменить его не может. Балансировщик использует один служебный token для всех очередей core; достаточно хранить его хеш. Отдельных токенов очередей нет.
- Индексы: внешние ключи дочерних таблиц (`file.record_id`, `fragment.record_id`, `fragment.file_id`, `fragment.parent_fragment_id`, `element.record_id`, `element.file_id`, `element.fragment_id`, `text.*_id`, `chunk.*_id`, `task.element_id`) для проверки готовности; частичные индексы очередей `task (type, created_at) WHERE status = 'wait'` и `chunk (created_at) WHERE status = 'wait'`; частичные индексы `task (id) WHERE status = 'processing'` и `chunk (id) WHERE status = 'processing'` для общего возврата всех взятых задач по `POST /api/task-sources/reset` и при старте core; частичные индексы `WHERE status = 'error'` на `fragment`, `element`, `text`, `chunk` и `task` для цикла очистки по таймеру; частичный индекс `file (finished_at) WHERE status = 'error'` для списка ошибок внутри записей в `done`; индекс HNSW `chunk USING hnsw (embed halfvec_cosine_ops)` для семантического поиска; частичный индекс `record (id) WHERE status = 'parsing'` для удаления прерванных записей при старте; частичный индекс `record (processed_at) WHERE status = 'error'` для списка ошибочных записей в веб-интерфейсе; уникальный индекс `record(owner, source_id, source_url)`.
- Ограничения: у `chunk` заполнены ровно один родитель и ровно одно из `text` и `is_picture`, длина текста ограничена; у `text` набор заполненных ссылок согласован с `kind` (см. 1.5); в `wait` поля `taken_at` и `taken_by` пусты, в `processing` заполнены; иерархические правила из 1.1 проверяются при построении дерева в коде (на уровне БД — при необходимости constraint-триггерами).
- Эмбеддинги лежат в столбце типа `halfvec(2048)`: 2048 — размерность Qwen3-VL-Embedding-2B (см. таблицу моделей). Индексы pgvector (HNSW, IVFFlat) по типу `vector` ограничены 2000 измерениями, а по `halfvec` — 4000. В `halfvec` каждое число занимает 16 бит вместо 32: вектор весит 4 КБ вместо 8, а точности хватает, так как модель и так считает в BF16. Если сменить модель на размерность больше 4000, векторы придётся урезать (MRL). В chunk хранится название модели (`embed_model`), чтобы при смене модели можно было пересчитать эмбеддинги.
- Входные данные самостоятельных задач агента хранятся в core отдельно от каталогов record, с адресацией по agent_id/task_id; текстовый embed принимается как байты UTF-8. Результаты и ошибки хранятся в `agent_task`. Сроки хранения, квоты, удаление и очистка будут определены отдельно. Сложность собственных задач core вычисляется по данным task/chunk до выдачи; способ её хранения или кеширования пока не закреплён.
- Эскиз таблиц:

```sql
CREATE TYPE record_status    AS ENUM ('parsing','processing','done','error');  -- record
CREATE TYPE container_status AS ENUM ('processing','done','error');          -- file, fragment, element, text
CREATE TYPE task_status      AS ENUM ('wait','processing','done','error');   -- chunk, task
CREATE TYPE element_kind     AS ENUM ('photo','audio');
CREATE TYPE task_type        AS ENUM ('transcribe','ocr');
CREATE TYPE text_kind        AS ENUM ('record','element','task_result');

CREATE TABLE app_user (
  id             uuid PRIMARY KEY,
  login          text NOT NULL UNIQUE,  -- нормализованный логин
  password_hash  text NOT NULL,
  role           text NOT NULL CHECK (role IN ('user', 'admin')),
  enabled        boolean NOT NULL DEFAULT true
);

CREATE TABLE registration_settings (
  singleton          boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  registration_mode  text NOT NULL DEFAULT 'closed'
    CHECK (registration_mode IN ('closed', 'open', 'invite'))
);
INSERT INTO registration_settings (singleton) VALUES (true);

CREATE TABLE invitation (
  id          uuid PRIMARY KEY,
  code_hash   text NOT NULL UNIQUE,
  created_by  uuid NOT NULL REFERENCES app_user(id),
  used_by     uuid UNIQUE REFERENCES app_user(id),
  used_at     timestamptz,
  revoked     boolean NOT NULL DEFAULT false,
  CHECK ((used_by IS NULL) = (used_at IS NULL))
);

CREATE TABLE agent (
  agent_id       uuid PRIMARY KEY,  -- задаётся при создании, совпадает с task_source_id
  token_hash     text NOT NULL UNIQUE,  -- хеш автоматически созданного token
  owner          uuid NOT NULL REFERENCES app_user(id),
  mode           text NOT NULL,  -- отдельная регистрация каждого режима
  trust_level    integer NOT NULL CHECK (trust_level >= 0),
  enabled        boolean NOT NULL DEFAULT true,
  deleted_at     timestamptz  -- регистрация сохраняется после окончательного удаления
);

CREATE TABLE scraper (
  scrapper_id    uuid PRIMARY KEY,
  owner         uuid NOT NULL REFERENCES app_user(id),
  source_id     text NOT NULL,
  web_endpoint  text,
  token_hash    text NOT NULL UNIQUE,
  enabled       boolean NOT NULL DEFAULT true,
  deleted_at    timestamptz  -- удалённый скраппер нельзя включить повторно
);

-- Единственная служебная регистрация создаётся при настройке сервера.
CREATE TABLE balancer (
  balancer_id  text PRIMARY KEY,
  token_hash   text NOT NULL UNIQUE,
  enabled      boolean NOT NULL DEFAULT true
);
-- Этот token применяется ко всем очередям; отдельных секретов источников нет.

-- Самостоятельные задачи агентов не являются листьями дерева record.
CREATE TABLE agent_task (
  id           uuid PRIMARY KEY,
  agent_id     uuid NOT NULL REFERENCES agent(agent_id),
  type         text NOT NULL CHECK (type IN ('transcribe', 'ocr', 'embed')),
  status       task_status NOT NULL DEFAULT 'wait',
  input_mime   text NOT NULL,
  complexity   double precision, -- вычисляет core до claim; формулы пока открыты
  payload      jsonb,
  error        jsonb,
  taken_at     timestamptz,
  taken_by     text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  CHECK (complexity IS NULL OR (complexity >= 0 AND complexity < 'Infinity'::float8)),
  CHECK (status <> 'wait' OR (taken_at IS NULL AND taken_by IS NULL)),
  CHECK (status <> 'processing' OR (taken_at IS NOT NULL AND taken_by IS NOT NULL AND complexity IS NOT NULL))
);
CREATE INDEX ON agent_task (agent_id, type, created_at, id) WHERE status = 'wait';
CREATE INDEX ON agent_task (agent_id, taken_by) WHERE status = 'processing';

CREATE TABLE record (
  id                 uuid PRIMARY KEY,
  owner              uuid NOT NULL REFERENCES app_user(id),
  scrapper           uuid NOT NULL REFERENCES scraper(scrapper_id) ON DELETE RESTRICT,
  trust_level        integer NOT NULL CHECK (trust_level >= 0),
  source_id          text NOT NULL,
  source_url         text NOT NULL,
  source_created_at  timestamptz,
  scraped_at         timestamptz NOT NULL,
  registered_at      timestamptz NOT NULL,
  processed_at       timestamptz,
  status             record_status NOT NULL DEFAULT 'parsing',
  title              text,
  author             text,
  parent_source_url  text,
  metadata           jsonb,
  error              text,
  error_detail       jsonb,
  UNIQUE (owner, source_id, source_url)
);

CREATE INDEX ON record (owner, trust_level, id);
CREATE INDEX ON record (scrapper);
CREATE INDEX ON agent (owner);
CREATE INDEX ON scraper (owner);

CREATE TABLE file (
  id           uuid PRIMARY KEY,
  record_id    uuid NOT NULL REFERENCES record(id) ON DELETE CASCADE,
  ord          int  NOT NULL DEFAULT 0,
  status       container_status NOT NULL DEFAULT 'processing',
  error        text,
  error_detail jsonb,
  name         text,
  mime         text,
  size         bigint,
  finished_at  timestamptz,
  meta         jsonb
);

CREATE TABLE fragment (
  id                 uuid PRIMARY KEY,
  record_id          uuid NOT NULL REFERENCES record(id) ON DELETE CASCADE,
  file_id            uuid NOT NULL REFERENCES file(id) ON DELETE CASCADE,
  parent_fragment_id uuid REFERENCES fragment(id) ON DELETE CASCADE,  -- NULL на верхнем уровне
  ord                int  NOT NULL DEFAULT 0,
  status             container_status NOT NULL DEFAULT 'processing',
  finished_at        timestamptz,
  meta               jsonb
);

CREATE TABLE element (              -- photo и audio
  id           uuid PRIMARY KEY,
  record_id    uuid NOT NULL REFERENCES record(id)   ON DELETE CASCADE,
  file_id      uuid NOT NULL REFERENCES file(id)     ON DELETE CASCADE,
  fragment_id  uuid NOT NULL REFERENCES fragment(id) ON DELETE CASCADE,
  kind         element_kind NOT NULL,
  ord          int  NOT NULL DEFAULT 0,
  status       container_status NOT NULL DEFAULT 'processing',
  finished_at  timestamptz,
  meta         jsonb
);

CREATE TABLE text (
  id           uuid PRIMARY KEY,
  kind         text_kind NOT NULL,
  record_id    uuid NOT NULL REFERENCES record(id) ON DELETE CASCADE,
  fragment_id  uuid REFERENCES fragment(id) ON DELETE CASCADE,  -- пусто только у собственного text записи
  element_id   uuid REFERENCES element(id)  ON DELETE CASCADE,  -- только у результата задачи
  status       container_status NOT NULL DEFAULT 'processing',
  finished_at  timestamptz,
  meta         jsonb,
  CHECK ((kind = 'record'      AND fragment_id IS NULL     AND element_id IS NULL)
      OR (kind = 'element'     AND fragment_id IS NOT NULL AND element_id IS NULL)
      OR (kind = 'task_result' AND fragment_id IS NOT NULL AND element_id IS NOT NULL))
);

CREATE TABLE chunk (
  id           uuid PRIMARY KEY,
  text_id      uuid REFERENCES text(id)    ON DELETE CASCADE,
  element_id   uuid REFERENCES element(id) ON DELETE CASCADE,   -- chunk-картинка photo
  ord          int  NOT NULL DEFAULT 0,
  status       task_status NOT NULL DEFAULT 'wait',
  text         text,
  is_picture   boolean NOT NULL DEFAULT false,
  embed        halfvec(2048),
  embed_model  text,
  taken_at     timestamptz,
  taken_by     text,                                            -- balancer_id
  created_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  CHECK ((text_id    IS NOT NULL AND element_id IS NULL AND text IS NOT NULL AND NOT is_picture)
      OR (element_id IS NOT NULL AND text_id    IS NULL AND text IS NULL     AND is_picture)),
  CHECK (status <> 'wait' OR (taken_at IS NULL AND taken_by IS NULL)),
  CHECK (status <> 'processing' OR (taken_at IS NOT NULL AND taken_by IS NOT NULL))
);

CREATE INDEX ON chunk USING hnsw (embed halfvec_cosine_ops);

CREATE TABLE task (
  id           uuid PRIMARY KEY,
  element_id   uuid NOT NULL REFERENCES element(id) ON DELETE CASCADE,
  type         task_type NOT NULL,
  status       task_status NOT NULL DEFAULT 'wait',
  taken_at     timestamptz,
  taken_by     text,                                            -- balancer_id
  created_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  CHECK (status <> 'wait' OR (taken_at IS NULL AND taken_by IS NULL)),
  CHECK (status <> 'processing' OR (taken_at IS NOT NULL AND taken_by IS NOT NULL))
);
```

