-- Миграция 004 · workflow v5: состояние бота в PostgreSQL вместо static data и Data Table
-- Идемпотентная: можно запускать повторно.

-- Текущая версия выжимки (после правок, до подтверждения), её текст для Telegram, число правок
ALTER TABLE app.meetings ADD COLUMN IF NOT EXISTS summary_current      jsonb;
ALTER TABLE app.meetings ADD COLUMN IF NOT EXISTS summary_text         text;
ALTER TABLE app.meetings ADD COLUMN IF NOT EXISTS corrections          smallint NOT NULL DEFAULT 0;
ALTER TABLE app.meetings ADD COLUMN IF NOT EXISTS audio_file_unique_id text;   -- стабильный ID файла в Telegram (вместо хэша)

-- Новый статус: голосовое расшифровано, ждём дату встречи
ALTER TABLE app.meetings DROP CONSTRAINT IF EXISTS meetings_status_check;
ALTER TABLE app.meetings ADD CONSTRAINT meetings_status_check
    CHECK (status IN ('received','transcribed','awaiting_date','summarized','confirmed','edited','failed'));

-- Что бот ждёт от чата: правку к выжимке или дату встречи текстом (30 минут)
CREATE TABLE IF NOT EXISTS app.chat_state (
    tg_chat_id      bigint PRIMARY KEY,
    pending_action  text NOT NULL CHECK (pending_action IN ('edit','date')),
    meeting_id      uuid NOT NULL REFERENCES app.meetings(meeting_id) ON DELETE CASCADE,
    expires_at      timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Этап в журнале запусков: распознавание, выжимка, правка
ALTER TABLE app.pipeline_runs ADD COLUMN IF NOT EXISTS stage text;
ALTER TABLE app.pipeline_runs DROP CONSTRAINT IF EXISTS pipeline_runs_stage_check;
ALTER TABLE app.pipeline_runs ADD CONSTRAINT pipeline_runs_stage_check CHECK (stage IN ('asr','summary','edit'));

INSERT INTO app.prompts (prompt_version, model, notes)
VALUES ('v3', 'GigaChat-2-Max', 'Промпт v3 в узле «Собрать запрос» workflow v5; «Дата заметки» = дата встречи')
ON CONFLICT (prompt_version) DO NOTHING;

-- Сводка по запускам с разбивкой по этапам
DROP VIEW IF EXISTS app.v_runs_daily;
CREATE VIEW app.v_runs_daily AS
SELECT date_trunc('day', started_at)::date AS day,
       count(*) FILTER (WHERE stage = 'asr')                 AS asr_runs,
       count(*) FILTER (WHERE stage IN ('summary','edit'))   AS llm_runs,
       count(*) FILTER (WHERE status IN ('ok','retry_ok'))   AS ok_runs,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY asr_ms) FILTER (WHERE stage = 'asr')               AS asr_p95_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY llm_ms) FILTER (WHERE stage IN ('summary','edit')) AS llm_p95_ms,
       sum(total_tokens) AS tokens
FROM app.pipeline_runs GROUP BY 1;

INSERT INTO app.schema_migrations (version, note) VALUES ('004_v5_bot', 'workflow v5: chat_state, summary_current, awaiting_date, stage')
ON CONFLICT (version) DO NOTHING;
