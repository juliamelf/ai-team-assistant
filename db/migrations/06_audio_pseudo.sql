-- Миграция 006 · решения по ПДн от 12.11.2026
-- 1) Аудио встреч храним 3 месяца: в отдельной таблице, с SHA-256; очистка — app.purge_audio().
-- 2) Имена заменяем на метки перед GigaChat: контекст без ФИО app.prompt_context_pseudo() (подключение — workflow v6).

CREATE TABLE IF NOT EXISTS app.meeting_audio (
    meeting_id  uuid PRIMARY KEY REFERENCES app.meetings(meeting_id) ON DELETE CASCADE,
    mime_type   text,
    size_bytes  integer NOT NULL,
    sha256      text NOT NULL,
    audio       bytea NOT NULL,
    stored_at   timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL DEFAULT now() + interval '3 months'
);
CREATE INDEX IF NOT EXISTS meeting_audio_expires_idx ON app.meeting_audio (expires_at);

-- Когда аудио удалено, в meetings остаются хэш, длительность и отметка об удалении
ALTER TABLE app.meetings ADD COLUMN IF NOT EXISTS audio_deleted_at timestamptz;

-- Сохранить аудио встречи (base64 из n8n). Повтор ничего не меняет. Возвращает хэш.
CREATE OR REPLACE FUNCTION app.save_audio(p_meeting_id uuid, p_mime text, p_b64 text)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE
    v_bytes bytea := decode(p_b64, 'base64');
    v_hash  text  := encode(sha256(v_bytes), 'hex');
BEGIN
    INSERT INTO app.meeting_audio (meeting_id, mime_type, size_bytes, sha256, audio)
    VALUES (p_meeting_id, p_mime, length(v_bytes), v_hash, v_bytes)
    ON CONFLICT (meeting_id) DO NOTHING;
    UPDATE app.meetings SET audio_sha256 = v_hash WHERE meeting_id = p_meeting_id AND audio_sha256 IS NULL;
    RETURN v_hash;
END $$;

-- Удалить аудио с истёкшим сроком. Запускается раз в сутки отдельным workflow.
CREATE OR REPLACE FUNCTION app.purge_audio()
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
    WITH gone AS (DELETE FROM app.meeting_audio WHERE expires_at <= now() RETURNING meeting_id)
    UPDATE app.meetings m SET audio_deleted_at = now() FROM gone WHERE m.meeting_id = gone.meeting_id;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;

-- Замена имён на метки делается в n8n (JS, n8n/v6/pseudonymize.js) по карте alias_map из app.prompt_context:
-- регулярные выражения PostgreSQL для кириллицы зависят от локали кластера.

-- Контекст для промпта без ФИО: команда по ID, должностям и направлениям (для v6 с псевдонимизацией)
CREATE OR REPLACE FUNCTION app.prompt_context_pseudo(p_date date)
RETURNS jsonb LANGUAGE sql STABLE AS $$
    SELECT (app.prompt_context(p_date) - 'team' - 'alias_map') || jsonb_build_object(
        'team', (SELECT jsonb_agg(jsonb_build_object('employee_id', e.employee_id, 'position', e.position,
                                                     'direction', e.direction, 'is_manager', e.is_manager)
                                  ORDER BY e.is_manager DESC, e.employee_id)
                 FROM app.employees e
                 WHERE (e.active_from IS NULL OR e.active_from <= p_date)
                   AND (e.active_to   IS NULL OR e.active_to   >= p_date)))
$$;

INSERT INTO app.schema_migrations (version, note) VALUES ('006_audio_pseudo', 'аудио 3 месяца + SHA-256; псевдонимизация перед GigaChat')
ON CONFLICT (version) DO NOTHING;
