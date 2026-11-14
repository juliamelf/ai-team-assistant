-- Миграция 005 · дата встречи из речи (meeting_date_source = 'speech')
ALTER TABLE app.meetings DROP CONSTRAINT IF EXISTS meetings_meeting_date_source_check;
ALTER TABLE app.meetings ADD CONSTRAINT meetings_meeting_date_source_check
    CHECK (meeting_date_source IN ('speech','button','caption','message_date'));
INSERT INTO app.schema_migrations (version, note) VALUES ('005_speech_date', 'дата встречи из речи, смена даты до подтверждения')
ON CONFLICT (version) DO NOTHING;
