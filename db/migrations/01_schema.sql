-- ИИ-ассистент руководителя · хранилище MVP (задача e03) · PostgreSQL 16+
-- Схемы: app — рабочие данные и справочники; pii — имена сотрудников и их формы (ПДн отдельно).
-- Скрипт идемпотентный: повторный запуск ничего не ломает.

CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS pii;

CREATE TABLE IF NOT EXISTS app.schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    note        text
);

-- ================= СПРАВОЧНИКИ =================

-- D04 · состав команды (без ФИО)
CREATE TABLE IF NOT EXISTS app.employees (
    employee_id  text PRIMARY KEY CHECK (employee_id ~ '^[EM][0-9]{2}$'),
    team         text NOT NULL DEFAULT 'Заказы',
    position     text NOT NULL,
    direction    text[] NOT NULL DEFAULT '{}',
    level        smallint CHECK (level BETWEEN 0 AND 5),
    manager_id   text REFERENCES app.employees(employee_id),
    is_manager   boolean NOT NULL DEFAULT false,
    active_from  date,
    active_to    date,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CHECK (active_to IS NULL OR active_from IS NULL OR active_to >= active_from)
);

-- D04 · ФИО и короткие варианты для промпта
CREATE TABLE IF NOT EXISTS pii.employee_names (
    employee_id  text PRIMARY KEY REFERENCES app.employees(employee_id) ON DELETE CASCADE,
    full_name    text NOT NULL,
    first_name   text NOT NULL,
    last_name    text NOT NULL,
    aliases      text[] NOT NULL DEFAULT '{}'
);

-- D04 · все формы имён: псевдонимизация и нечёткий поиск
CREATE TABLE IF NOT EXISTS pii.employee_aliases (
    id                        bigserial PRIMARY KEY,
    alias                     text NOT NULL,
    alias_lc                  text NOT NULL,
    employee_id               text NOT NULL REFERENCES app.employees(employee_id) ON DELETE CASCADE,
    alias_type                text NOT NULL CHECK (alias_type IN ('full_name','first_name','surname','diminutive','pronoun','asr_error')),
    grammatical_case          text CHECK (grammatical_case IN ('nomn','gent','datv','accs','ablt','loct')),
    use_for_pseudonymization  boolean NOT NULL DEFAULT true,
    UNIQUE (alias_lc, employee_id)
);
CREATE INDEX IF NOT EXISTS employee_aliases_lc_idx ON pii.employee_aliases (alias_lc);

-- D05 · колонки матрицы
CREATE TABLE IF NOT EXISTS app.skills (
    skill_id      text PRIMARY KEY CHECK (skill_id ~ '^[a-z_]+$'),
    matrix_order  smallint NOT NULL UNIQUE,
    name          text NOT NULL UNIQUE,
    matrix_sheet  text,
    description   text,
    aliases       text[] NOT NULL DEFAULT '{}'
);

-- D05 · синонимы: навык / наблюдение / не навык
CREATE TABLE IF NOT EXISTS app.skill_aliases (
    id        bigserial PRIMARY KEY,
    alias     text NOT NULL,
    alias_lc  text NOT NULL UNIQUE,
    target    text NOT NULL CHECK (target IN ('skill','observation','ignore')),
    skill_id  text REFERENCES app.skills(skill_id),
    note      text,
    CHECK ((target = 'skill') = (skill_id IS NOT NULL))
);

-- D06 · уровни по матрице (снимки; текущий = последний as_of)
CREATE TABLE IF NOT EXISTS app.skill_levels (
    employee_id  text NOT NULL REFERENCES app.employees(employee_id),
    skill_id     text NOT NULL REFERENCES app.skills(skill_id),
    level        smallint NOT NULL CHECK (level BETWEEN 0 AND 5),
    as_of        date NOT NULL,
    source       text NOT NULL,
    PRIMARY KEY (employee_id, skill_id, as_of)
);

-- D08 · спринты
CREATE TABLE IF NOT EXISTS app.sprints (
    sprint_id          text PRIMARY KEY,
    team               text NOT NULL DEFAULT 'Заказы',
    start_date         date NOT NULL UNIQUE,
    end_date           date NOT NULL,
    last_working_date  date,
    working_days       smallint,
    label              text
);

-- D08 · производственный календарь
CREATE TABLE IF NOT EXISTS app.calendar (
    date               date PRIMARY KEY,
    weekday_num        smallint NOT NULL CHECK (weekday_num BETWEEN 1 AND 7),
    weekday            text NOT NULL,
    day_type           text NOT NULL CHECK (day_type IN ('working','short','weekend','holiday','transfer_off','working_weekend')),
    is_working         boolean NOT NULL,
    work_hours         smallint NOT NULL,
    holiday_name       text,
    iso_week           text,
    week_start         date NOT NULL,
    sprint_id          text REFERENCES app.sprints(sprint_id),
    working_day_index  integer NOT NULL
);
CREATE INDEX IF NOT EXISTS calendar_wdi_idx ON app.calendar (working_day_index) WHERE is_working;

-- D09 · версии промпта
CREATE TABLE IF NOT EXISTS app.prompts (
    prompt_version  text PRIMARY KEY,
    model           text,
    body            text,
    notes           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ================= ВСТРЕЧИ =================

-- D01–D03, D10, D11, D12 · одна строка на голосовое
CREATE TABLE IF NOT EXISTS app.meetings (
    meeting_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tg_chat_id           bigint NOT NULL,
    tg_message_id        bigint NOT NULL,
    received_at          timestamptz NOT NULL DEFAULT now(),
    employee_id          text REFERENCES app.employees(employee_id),
    manager_id           text REFERENCES app.employees(employee_id),
    meeting_date         date,
    meeting_date_source  text CHECK (meeting_date_source IN ('button','caption','message_date')),
    audio_sha256         text,
    audio_duration_s     numeric(7,1),
    transcript           text,
    prompt_version       text REFERENCES app.prompts(prompt_version),
    summary_draft        jsonb,     -- ответ модели до правок (D12 «до»)
    summary_final        jsonb,     -- подтверждённая версия (D12 «после»)
    status               text NOT NULL DEFAULT 'received'
                         CHECK (status IN ('received','transcribed','summarized','confirmed','edited','failed')),
    confirmed_at         timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tg_chat_id, tg_message_id)   -- дедупликация повторной отправки (D02)
);
CREATE INDEX IF NOT EXISTS meetings_employee_idx ON app.meetings (employee_id, meeting_date);

-- Задачи и договорённости (D11 → D07)
CREATE TABLE IF NOT EXISTS app.meeting_tasks (
    task_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id   uuid NOT NULL REFERENCES app.meetings(meeting_id) ON DELETE CASCADE,
    item_no      smallint NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('task','agreement')),
    what         text NOT NULL,
    due_type     text NOT NULL CHECK (due_type IN ('date','range','from','event','recurring','none')),
    due_date     date,
    due_date_to  date,
    due_event    text,
    due_said     text,
    evidence     text,
    status       text NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','cancelled')),
    closed_at    timestamptz,
    UNIQUE (meeting_id, item_no),
    CHECK (due_type <> 'date'  OR due_date IS NOT NULL),
    CHECK (due_type <> 'range' OR (due_date IS NOT NULL AND due_date_to IS NOT NULL AND due_date_to >= due_date))
);

CREATE TABLE IF NOT EXISTS app.task_owners (
    task_id      uuid NOT NULL REFERENCES app.meeting_tasks(task_id) ON DELETE CASCADE,
    employee_id  text NOT NULL REFERENCES app.employees(employee_id),
    PRIMARY KEY (task_id, employee_id)
);

-- D13 · события динамической матрицы
CREATE TABLE IF NOT EXISTS app.skill_events (
    event_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id   uuid NOT NULL REFERENCES app.meetings(meeting_id) ON DELETE CASCADE,
    employee_id  text NOT NULL REFERENCES app.employees(employee_id),
    skill_id     text NOT NULL REFERENCES app.skills(skill_id),
    direction    text NOT NULL CHECK (direction IN ('up','down')),
    evidence     text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Наблюдения (вне матрицы) и сигналы
CREATE TABLE IF NOT EXISTS app.meeting_signals (
    signal_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id   uuid NOT NULL REFERENCES app.meetings(meeting_id) ON DELETE CASCADE,
    kind         text NOT NULL CHECK (kind IN ('observation','signal')),
    employee_id  text REFERENCES app.employees(employee_id),
    signal_type  text CHECK (signal_type IN ('attrition_risk','burnout','motivation','goal','conflict','mood','probation','other')),
    direction    text CHECK (direction IN ('up','down')),
    text         text NOT NULL,
    evidence     text,
    CHECK ((kind = 'signal') = (signal_type IS NOT NULL))
);

-- ================= ЭКСПЛУАТАЦИЯ =================

-- D17 · лог запусков без текста встречи
CREATE TABLE IF NOT EXISTS app.pipeline_runs (
    run_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id         uuid REFERENCES app.meetings(meeting_id) ON DELETE SET NULL,
    tg_message_id      bigint,
    started_at         timestamptz NOT NULL DEFAULT now(),
    finished_at        timestamptz,
    asr_ms             integer,
    llm_ms             integer,
    llm_calls          smallint,
    prompt_tokens      integer,
    completion_tokens  integer,
    total_tokens       integer,
    model              text,
    prompt_version     text,
    status             text NOT NULL CHECK (status IN ('ok','retry_ok','invalid_json','asr_failed','llm_failed','db_failed')),
    error              text
);
CREATE INDEX IF NOT EXISTS pipeline_runs_started_idx ON app.pipeline_runs (started_at);

INSERT INTO app.schema_migrations (version, note) VALUES ('001_schema', 'MVP: справочники, встречи, запуски')
ON CONFLICT (version) DO NOTHING;
