### 7. Хранение и технологии

Рекомендуется PostgreSQL с расширением pgvector версии не ниже 0.7 (нужен тип `halfvec`, см. ниже); СУБД пока не зафиксирована. Фрагменты SQL в этом разделе — эскизы для PostgreSQL.

- Типы `record_status`, `container_status`, `task_status`, `element_kind`, `task_type`, `text_kind` — enum. Enum — тип поля, которое может принимать только одно значение из заданного списка. В SQLite enum нет, там используется `TEXT` с проверкой `CHECK`.
- Каждый тип объектов дерева лежит в своей таблице: `record`, `file`, `fragment`, `text`, `element`, `chunk`, `task`. Внешние ключи с `ON DELETE CASCADE`: авторизованное удаление записи (см. 6 и 11.5) — это удаление строки record, остальное удаляется каскадом. Цикл очистки удаляет строки явно, снизу вверх (2.6); каскад для него — подстраховка.
- Исходные файлы лежат в долговременном хранилище core по пути `/storage/<source_id>/<record_id>/<file_id>.<исходное расширение>`; ни пути, ни хэша в БД нет. Данные для задач лежат во временном `task_storage` на файловой системе (не в БД; путь выводится из `record_id` и `element_id`) и удаляются очисткой хранилищ целым каталогом записи (3.3).
- Все поля с данными, специфичными для источника или типа файла (`metadata`, `meta`), имеют тип JSONB.
- Пользователи, настройки регистрации, приглашения и регистрации агентов/скрапперов хранятся в core, а не в параметрах клиента. Owner у record, agent и scraper — внешний ключ на app_user. Индекс record(owner, trust_level, id) поддерживает обязательный фильтр доступа; индексы agent(owner) и scraper(owner) — списки в кабинете. Между пользователями общих record и таблицы связи владельцев нет. Удаление пользователя не должно каскадно уничтожать его записи: вместо этого учётную запись блокируют; отдельный сценарий окончательного удаления данных не входит в этот контракт.
- Индексы: внешние ключи дочерних таблиц (`file.record_id`, `fragment.record_id`, `fragment.file_id`, `fragment.parent_fragment_id`, `element.record_id`, `element.file_id`, `element.fragment_id`, `text.*_id`, `chunk.*_id`, `task.element_id`) для проверки готовности; частичные индексы очередей `task (type, created_at) WHERE status = 'wait'` и `chunk (created_at) WHERE status = 'wait'`; частичные индексы `task (taken_by) WHERE status = 'processing'` и `chunk (taken_by) WHERE status = 'processing'` для возврата задач по `POST /api/task-sources/{task_source_id}/reset` и при старте core; частичные индексы `WHERE status = 'error'` на `fragment`, `element`, `text`, `chunk` и `task` для цикла очистки по таймеру; частичный индекс `file (finished_at) WHERE status = 'error'` для списка ошибок внутри записей в `done`; индекс HNSW `chunk USING hnsw (embed halfvec_cosine_ops)` для семантического поиска; частичный индекс `record (id) WHERE status = 'parsing'` для удаления прерванных записей при старте; частичный индекс `record (processed_at) WHERE status = 'error'` для списка ошибочных записей в веб-интерфейсе; уникальный индекс `record(owner, source_id, source_url)`.
- Ограничения: у `chunk` заполнены ровно один родитель и ровно одно из `text` и `is_picture`, длина текста ограничена; у `text` набор заполненных ссылок согласован с `kind` (см. 1.5); в `wait` поля `taken_at` и `taken_by` пусты, в `processing` заполнены; иерархические правила из 1.1 проверяются при построении дерева в коде (на уровне БД — при необходимости constraint-триггерами).
- Эмбеддинги лежат в столбце типа `halfvec(2048)`: 2048 — размерность Qwen3-VL-Embedding-2B (см. таблицу моделей). Индексы pgvector (HNSW, IVFFlat) по типу `vector` ограничены 2000 измерениями, а по `halfvec` — 4000. В `halfvec` каждое число занимает 16 бит вместо 32: вектор весит 4 КБ вместо 8, а точности хватает, так как модель и так считает в BF16. Если сменить модель на размерность больше 4000, векторы придётся урезать (MRL). В chunk хранится название модели (`embed_model`), чтобы при смене модели можно было пересчитать эмбеддинги.
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
  id             uuid PRIMARY KEY,  -- несекретный task_source_id
  agent_id_hash  text NOT NULL UNIQUE,  -- хеш выданного core секретного agent_id
  owner          uuid NOT NULL REFERENCES app_user(id),
  mode           text NOT NULL,  -- отдельная регистрация каждого режима
  trust_level    integer NOT NULL CHECK (trust_level >= 0),
  task_endpoint  text NOT NULL,  -- общий адрес допустим, маршрутизация по task_source_id
  enabled        boolean NOT NULL DEFAULT true
);

CREATE TABLE scraper (
  scraper_id    uuid PRIMARY KEY,
  owner         uuid NOT NULL REFERENCES app_user(id),
  source_id     text NOT NULL,
  web_endpoint  text,
  secret_hash   text NOT NULL UNIQUE,
  enabled       boolean NOT NULL DEFAULT true
);

CREATE TABLE record (
  id                 uuid PRIMARY KEY,
  owner              uuid NOT NULL REFERENCES app_user(id),
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

