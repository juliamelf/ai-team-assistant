-- Миграция 007 · схема выжимки 1.0 (решения 13.11.2026)
-- Документ: «ИИ-ассистент / Схема выжимки 1.0 — решения и правки».
--
-- 1. Словарь тем вместо 8 навыков демо-матрицы (app.topics). Модель возвращает в skills[].skill_id тему,
--    тема знает компетенцию матрицы в каждом треке. Перевод события в компетенцию по треку сотрудника —
--    задача «Матрица компетенций в базе» (там появятся компетенции и треки сотрудников).
-- 2. Синонимы навыков перепривязаны к темам. Пометки «вне матрицы» для SQL, баг-репортов, оценки
--    трудозатрат, анализа логов, архитектуры фреймворка, тестирования требований и презентации
--    сняты: в актуальной матрице это компетенции.
-- 3. save_summary под контракт 1.0: срок value + value_to, said для event и recurring,
--    направление same / up / down и assessment, сигналы с tone (risk / positive / neutral) и типом achievement,
--    обязательная цитата evidence. Все ошибки ответа собираются в одно сообщение для модели.
-- 4. Снимок уровней по 8 старым навыкам (app.skill_levels) очищен; представление v_skill_matrix
--    заменено на v_topic_events. Уровни по компетенциям — задача «Матрица компетенций в базе».
-- 5. Просрочка в v_open_tasks: для date — по due_date, для range — по due_date_to.
-- 6. Обновления задач с прошлых встреч (14.11.2026): блок task_updates — статус in_progress / done /
--    cancelled или перенос срока (rescheduled). Модель ссылается на задачу коротким ID T<номер> из списка
--    открытых задач в контексте; task_id = null — руководитель привяжет вручную. Текущий статус и срок
--    задачи считаются по последнему обновлению из подтверждённых встреч (app.refresh_task).
--
-- Идемпотентная. В служебном workflow установки узел с этой миграцией ставится ПОСЛЕДНИМ:
-- повторный запуск 02_seed и 03_functions возвращает старые синонимы и старую save_summary,
-- а эта миграция снова приводит их к схеме 1.0.

-- ================= 1. СЛОВАРЬ ТЕМ =================

CREATE TABLE IF NOT EXISTS app.topics (
    topic_id           text PRIMARY KEY CHECK (topic_id ~ '^[a-z][a-z0-9_]*$'),
    sort_order         smallint NOT NULL,
    name               text NOT NULL UNIQUE,
    speech_examples    text[] NOT NULL DEFAULT '{}',
    competency_manual  text CHECK (competency_manual ~ '^[A-E]\.[0-9]+$'),   -- трек «Ручное» и руководитель тестирования
    competency_auto    text CHECK (competency_auto   ~ '^[A-E]\.[0-9]+$'),   -- трек «Автоматизация»
    competency_load    text CHECK (competency_load   ~ '^[A-E]\.[0-9]+$'),   -- трек «Нагрузочное»
    basis              text,                                                -- на чём основана привязка в матрице
    updated_at         timestamptz NOT NULL DEFAULT now()
);

INSERT INTO app.topics (topic_id, sort_order, name, speech_examples, competency_manual, competency_auto, competency_load, basis) VALUES
--@@TOPICS@@
ON CONFLICT (topic_id) DO UPDATE SET
    sort_order = EXCLUDED.sort_order, name = EXCLUDED.name, speech_examples = EXCLUDED.speech_examples,
    competency_manual = EXCLUDED.competency_manual, competency_auto = EXCLUDED.competency_auto,
    competency_load = EXCLUDED.competency_load, basis = EXCLUDED.basis, updated_at = now();

-- Старые 8 навыков → темы (для синонимов и событий, записанных до миграции)
CREATE TABLE IF NOT EXISTS app.skill_topic_map (
    skill_id  text PRIMARY KEY,
    topic_id  text NOT NULL REFERENCES app.topics(topic_id)
);
INSERT INTO app.skill_topic_map (skill_id, topic_id) VALUES
    ('test_design','test_design'), ('func_regress','regression'), ('api','api'), ('ui_auto','ui_auto'),
    ('python','programming'), ('cicd','cicd'), ('load','load_testing'), ('mobile','mobile')
ON CONFLICT (skill_id) DO UPDATE SET topic_id = EXCLUDED.topic_id;

-- ================= 2. СИНОНИМЫ =================

ALTER TABLE app.skill_aliases ADD COLUMN IF NOT EXISTS topic_id text REFERENCES app.topics(topic_id);
ALTER TABLE app.skill_aliases DROP CONSTRAINT IF EXISTS skill_aliases_target_check;
ALTER TABLE app.skill_aliases DROP CONSTRAINT IF EXISTS skill_aliases_check;
ALTER TABLE app.skill_aliases DROP CONSTRAINT IF EXISTS skill_aliases_target_ref_check;
-- 'skill' остаётся допустимым, чтобы повторный запуск старого 02_seed не падал; ниже такие строки переводятся в 'topic'
ALTER TABLE app.skill_aliases ADD CONSTRAINT skill_aliases_target_check
    CHECK (target IN ('topic','skill','observation','ignore'));
ALTER TABLE app.skill_aliases ADD CONSTRAINT skill_aliases_target_ref_check
    CHECK (target NOT IN ('topic','skill') OR coalesce(topic_id, skill_id) IS NOT NULL);

UPDATE app.skill_aliases a
   SET target = 'topic', topic_id = m.topic_id, skill_id = NULL
  FROM app.skill_topic_map m
 WHERE a.target = 'skill' AND a.skill_id = m.skill_id;

-- Уточнение: у регрессионной модели своя тема, а не общий регресс
UPDATE app.skill_aliases SET topic_id = 'regression_model'
 WHERE target = 'topic' AND topic_id = 'regression' AND alias_lc LIKE '%регрессионн%модел%';

-- Бывшие «вне матрицы», которые в актуальной матрице — компетенции. LIKE, а не регулярные выражения:
-- классы символов для кириллицы в PostgreSQL зависят от локали кластера.
UPDATE app.skill_aliases a
   SET target = 'topic', topic_id = r.topic_id
  FROM (VALUES
        ('test_data_sql',          ARRAY['%sql%','%эскуэль%','%esquel%','%тестовые данные%','%тестовых данных%']),
        ('bug_reports',            ARRAY['%баг-репорт%','%баг репорт%','%багрепорт%','%документирование дефект%','%оформление баг%']),
        ('estimation',             ARRAY['%трудозатрат%']),
        ('bug_localization',       ARRAY['%анализ лог%','%локализаци%']),
        ('autotest_architecture',  ARRAY['%архитектура фреймворк%','%архитектуре фреймворк%','%архитектура автотест%','%архитектуре автотест%']),
        ('requirements',           ARRAY['%тестирование требований%','%ревью требований%','%анализ требований%']),
        ('presentation',           ARRAY['%презентац%','%выступлени%'])
       ) AS r(topic_id, patterns)
 WHERE a.target = 'observation' AND a.alias_lc LIKE ANY (r.patterns);

-- Версия промпта v4: выжимки v4 ссылаются на неё. Текст промпта запишется вместе с workflow v6.
INSERT INTO app.prompts (prompt_version, model, notes)
VALUES ('v4', 'GigaChat-2-Max', 'Схема выжимки 1.0, словарь тем, справочники из БД, метки вместо имён. Текст — с workflow v6')
ON CONFLICT (prompt_version) DO NOTHING;

-- Уточнения по проверке синонимов на Amvera (13.11.2026): слова, у которых в словаре тем есть своя тема
UPDATE app.skill_aliases a
   SET target = 'topic', topic_id = r.topic_id, skill_id = NULL
  FROM (VALUES
        ('ручное тестирование',         'func_testing'),
        ('функциональное тестирование', 'func_testing'),
        ('покрытие тестами',            'coverage'),
        ('наставничество',              'mentoring'),
        ('управленческий опыт',         'team_process')
       ) AS r(alias_lc, topic_id)
 WHERE a.alias_lc = r.alias_lc;

-- Направления развития: целевой профиль по нескольким компетенциям, а не отдельный навык.
-- В речи это цель («хочет развиваться в тест-аналитику») — модель пишет её в signals с type = goal.
-- Целевые уровни нужны агенту ИПР: разрыв считается по каждой компетенции направления.
CREATE TABLE IF NOT EXISTS app.development_paths (
    path_id          text PRIMARY KEY CHECK (path_id ~ '^[a-z][a-z0-9_]*$'),
    name             text NOT NULL UNIQUE,
    speech_examples  text[] NOT NULL DEFAULT '{}',
    competencies     text[] NOT NULL,                                   -- коды компетенций матрицы
    level_from       smallint NOT NULL CHECK (level_from BETWEEN 0 AND 5),
    level_to         smallint NOT NULL CHECK (level_to BETWEEN 0 AND 5),
    note             text,
    CHECK (level_to >= level_from)
);
INSERT INTO app.development_paths (path_id, name, speech_examples, competencies, level_from, level_to, note) VALUES
    ('test_analyst', 'Тест-аналитик',
     ARRAY['тест-аналитика','тест-аналитик','тест аналитика','тест аналитик','тестовый аналитик'],
     ARRAY['B.1','B.2','B.3','B.4','B.5','B.6','C.1','C.2','C.3','C.4'], 3, 4,
     'Уровни 3–4 по компетенциям блоков B и C (решение 13.11.2026)')
ON CONFLICT (path_id) DO UPDATE SET
    name = EXCLUDED.name, speech_examples = EXCLUDED.speech_examples, competencies = EXCLUDED.competencies,
    level_from = EXCLUDED.level_from, level_to = EXCLUDED.level_to, note = EXCLUDED.note;

-- Слова направлений развития не должны оставаться синонимами навыков или наблюдений
DELETE FROM app.skill_aliases a
 USING app.development_paths d
 WHERE a.alias_lc = ANY (SELECT lower(x) FROM unnest(d.speech_examples) x);

-- ================= 3. ПУНКТЫ ВЫЖИМКИ =================

-- Срок «с даты» без даты не сохраняется
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'meeting_tasks_from_check') THEN
        ALTER TABLE app.meeting_tasks ADD CONSTRAINT meeting_tasks_from_check CHECK (due_type <> 'from' OR due_date IS NOT NULL);
    END IF;
END $$;

-- События навыков: тема, направление same / up / down, оценка strong / weak при same
ALTER TABLE app.skill_events ADD COLUMN IF NOT EXISTS topic_id   text REFERENCES app.topics(topic_id);
ALTER TABLE app.skill_events ADD COLUMN IF NOT EXISTS assessment text;
ALTER TABLE app.skill_events ALTER COLUMN skill_id DROP NOT NULL;
UPDATE app.skill_events e SET topic_id = m.topic_id
  FROM app.skill_topic_map m WHERE e.topic_id IS NULL AND e.skill_id = m.skill_id;
ALTER TABLE app.skill_events DROP CONSTRAINT IF EXISTS skill_events_direction_check;
ALTER TABLE app.skill_events ADD CONSTRAINT skill_events_direction_check CHECK (direction IN ('up','down','same'));
ALTER TABLE app.skill_events DROP CONSTRAINT IF EXISTS skill_events_assessment_check;
ALTER TABLE app.skill_events ADD CONSTRAINT skill_events_assessment_check
    CHECK (assessment IS NULL OR (assessment IN ('strong','weak') AND direction = 'same'));
ALTER TABLE app.skill_events DROP CONSTRAINT IF EXISTS skill_events_topic_check;
ALTER TABLE app.skill_events ADD CONSTRAINT skill_events_topic_check CHECK (coalesce(topic_id, skill_id) IS NOT NULL);

-- Наблюдения и сигналы: same и assessment у наблюдений, tone и achievement у сигналов
ALTER TABLE app.meeting_signals ADD COLUMN IF NOT EXISTS assessment text;
ALTER TABLE app.meeting_signals ADD COLUMN IF NOT EXISTS tone       text;
ALTER TABLE app.meeting_signals DROP CONSTRAINT IF EXISTS meeting_signals_direction_check;
ALTER TABLE app.meeting_signals ADD CONSTRAINT meeting_signals_direction_check CHECK (direction IN ('up','down','same'));
ALTER TABLE app.meeting_signals DROP CONSTRAINT IF EXISTS meeting_signals_signal_type_check;
ALTER TABLE app.meeting_signals ADD CONSTRAINT meeting_signals_signal_type_check
    CHECK (signal_type IN ('attrition_risk','burnout','motivation','goal','conflict','mood','probation','achievement','other'));
ALTER TABLE app.meeting_signals DROP CONSTRAINT IF EXISTS meeting_signals_assessment_check;
ALTER TABLE app.meeting_signals ADD CONSTRAINT meeting_signals_assessment_check
    CHECK (assessment IS NULL OR (kind = 'observation' AND assessment IN ('strong','weak') AND direction = 'same'));
ALTER TABLE app.meeting_signals DROP CONSTRAINT IF EXISTS meeting_signals_tone_check;
ALTER TABLE app.meeting_signals ADD CONSTRAINT meeting_signals_tone_check
    CHECK (tone IS NULL OR (kind = 'signal' AND tone IN ('risk','positive','neutral')));

-- Короткий ID задачи для модели: T<task_no>
ALTER TABLE app.meeting_tasks ADD COLUMN IF NOT EXISTS task_no bigint GENERATED BY DEFAULT AS IDENTITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'meeting_tasks_task_no_key') THEN
        ALTER TABLE app.meeting_tasks ADD CONSTRAINT meeting_tasks_task_no_key UNIQUE (task_no);
    END IF;
END $$;
ALTER TABLE app.meeting_tasks DROP CONSTRAINT IF EXISTS meeting_tasks_status_check;
ALTER TABLE app.meeting_tasks ADD CONSTRAINT meeting_tasks_status_check
    CHECK (status IN ('open','in_progress','done','cancelled'));

-- Обновления задач с прошлых встреч (task_updates выжимки)
CREATE TABLE IF NOT EXISTS app.task_updates (
    update_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_id   uuid NOT NULL REFERENCES app.meetings(meeting_id) ON DELETE CASCADE,  -- встреча, на которой сказали
    item_no      smallint NOT NULL,
    task_id      uuid REFERENCES app.meeting_tasks(task_id) ON DELETE SET NULL,      -- null — не привязано
    what         text NOT NULL,
    status       text NOT NULL CHECK (status IN ('in_progress','done','cancelled','rescheduled')),
    due_type     text CHECK (due_type IN ('date','range','from','event','recurring','none')),
    due_date     date,
    due_date_to  date,
    due_event    text,
    due_said     text,
    evidence     text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (meeting_id, item_no),
    CHECK ((status = 'rescheduled') = (due_type IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS task_updates_task_idx ON app.task_updates (task_id);

-- ================= 4. СТАРЫЕ УРОВНИ =================

-- Снимок демо-матрицы по 8 навыкам на 01.09.2026 больше не соответствует матрице компетенций.
DELETE FROM app.skill_levels;
DROP VIEW IF EXISTS app.v_skill_matrix;

-- События по темам из подтверждённых встреч (до появления уровней по компетенциям)
CREATE OR REPLACE VIEW app.v_topic_events AS
SELECT ev.employee_id, ev.topic_id, t.name AS topic_name,
       t.competency_manual, t.competency_auto, t.competency_load,
       count(*) FILTER (WHERE ev.direction = 'up')                             AS ups,
       count(*) FILTER (WHERE ev.direction = 'down')                           AS downs,
       count(*) FILTER (WHERE ev.direction = 'same' AND ev.assessment = 'strong') AS same_strong,
       count(*) FILTER (WHERE ev.direction = 'same' AND ev.assessment = 'weak')   AS same_weak,
       count(*) FILTER (WHERE ev.direction = 'same' AND ev.assessment IS NULL)    AS same_unrated,
       max(m.meeting_date) AS last_event_date
FROM app.skill_events ev
JOIN app.meetings m USING (meeting_id)
JOIN app.topics t ON t.topic_id = ev.topic_id
WHERE m.status IN ('confirmed','edited')
GROUP BY ev.employee_id, ev.topic_id, t.name, t.competency_manual, t.competency_auto, t.competency_load;

-- ================= 5. ОТКРЫТЫЕ ЗАДАЧИ =================

-- Текущий срок задачи: последний перенос из подтверждённой встречи, иначе исходный
CREATE OR REPLACE VIEW app.v_task_state AS
SELECT t.task_id, 'T' || t.task_no AS task_ref, t.meeting_id, m.employee_id AS meeting_employee_id, m.meeting_date,
       t.kind, t.what, t.status, t.closed_at,
       coalesce(r.due_type,    t.due_type)    AS due_type,
       CASE WHEN r.update_id IS NULL THEN t.due_date    ELSE r.due_date    END AS due_date,
       CASE WHEN r.update_id IS NULL THEN t.due_date_to ELSE r.due_date_to END AS due_date_to,
       CASE WHEN r.update_id IS NULL THEN t.due_event   ELSE r.due_event   END AS due_event,
       CASE WHEN r.update_id IS NULL THEN t.due_said    ELSE r.due_said    END AS due_said,
       r.update_id IS NOT NULL AS rescheduled,
       (SELECT array_agg(o.employee_id ORDER BY o.employee_id) FROM app.task_owners o WHERE o.task_id = t.task_id) AS owner_ids
FROM app.meeting_tasks t
JOIN app.meetings m USING (meeting_id)
LEFT JOIN LATERAL (
    SELECT u.* FROM app.task_updates u JOIN app.meetings um USING (meeting_id)
    WHERE u.task_id = t.task_id AND u.status = 'rescheduled' AND um.status IN ('confirmed','edited')
    ORDER BY um.meeting_date DESC, u.created_at DESC LIMIT 1) r ON true
WHERE m.status IN ('confirmed','edited');

DROP VIEW IF EXISTS app.v_open_tasks;
CREATE VIEW app.v_open_tasks AS
SELECT meeting_employee_id, meeting_date, task_id, task_ref, kind, what, status,
       due_type, due_date, due_date_to, due_event, due_said, rescheduled, owner_ids,
       ((due_type = 'date'  AND due_date    < current_date) OR
        (due_type = 'range' AND due_date_to < current_date)) AS overdue
FROM app.v_task_state
WHERE status IN ('open','in_progress');

-- ================= 6. ФУНКЦИИ =================

-- Строка — существующая дата YYYY-MM-DD (2026-02-30 — нет)
CREATE OR REPLACE FUNCTION app.is_iso_date(p text)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF p IS NULL OR p !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN
        RETURN false;
    END IF;
    RETURN to_char(p::date, 'YYYY-MM-DD') = p;
EXCEPTION WHEN others THEN
    RETURN false;
END $$;

-- Ошибка в объекте срока (null — срок правильный). Общая для tasks и task_updates.
CREATE OR REPLACE FUNCTION app.due_error(d jsonb)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN jsonb_typeof(d) IS DISTINCT FROM 'object'
            THEN 'нужен объект due {type, value, value_to, said}'
        WHEN coalesce(d->>'type','') NOT IN ('date','range','from','event','recurring','none')
            THEN format('due.type "%s" не из списка date, range, from, event, recurring, none', coalesce(d->>'type','нет поля'))
        WHEN jsonb_typeof(d->'value') = 'array'
            THEN 'due.value — одна дата; конец диапазона пиши в due.value_to'
        WHEN d->>'type' IN ('date','range','from') AND NOT app.is_iso_date(d->>'value')
            THEN format('due.value "%s" должен быть датой YYYY-MM-DD из списка дат', coalesce(d->>'value','null'))
        WHEN d->>'type' = 'range' AND NOT app.is_iso_date(d->>'value_to')
            THEN format('для range нужен due.value_to — конец диапазона YYYY-MM-DD, получено "%s"', coalesce(d->>'value_to','null'))
        WHEN d->>'type' = 'range' AND (d->>'value_to')::date < (d->>'value')::date
            THEN 'due.value_to раньше due.value'
        WHEN d->>'type' IN ('date','from') AND d->>'value_to' IS NOT NULL
            THEN format('для %s поле due.value_to должно быть null', d->>'type')
        WHEN d->>'type' IN ('event','none') AND (d->>'value' IS NOT NULL OR d->>'value_to' IS NOT NULL)
            THEN format('для %s поля due.value и due.value_to должны быть null, слова о сроке — в due.said', d->>'type')
        WHEN d->>'type' = 'recurring'
             AND ((d->>'value' IS NOT NULL AND NOT app.is_iso_date(d->>'value'))
               OR (d->>'value_to' IS NOT NULL AND NOT app.is_iso_date(d->>'value_to')))
            THEN 'для recurring due.value и due.value_to — даты YYYY-MM-DD или null'
        WHEN d->>'type' IN ('event','recurring') AND coalesce(d->>'said','') = ''
            THEN format('для %s нужно due.said — слова руководителя («на следующей встрече», «раз в неделю по часу»)', d->>'type')
    END
$$;

-- Статус задачи по последнему обновлению из подтверждённых встреч (перенос срока статус не меняет)
CREATE OR REPLACE FUNCTION app.refresh_task(p_task uuid)
RETURNS void LANGUAGE sql AS $$
    UPDATE app.meeting_tasks t
       SET status    = coalesce(u.status, 'open'),
           closed_at = CASE WHEN u.status IN ('done','cancelled') THEN u.meeting_date::timestamptz END
      FROM (SELECT p_task AS task_id) k
      LEFT JOIN LATERAL (
          SELECT tu.status, um.meeting_date FROM app.task_updates tu JOIN app.meetings um USING (meeting_id)
          WHERE tu.task_id = p_task AND tu.status <> 'rescheduled' AND um.status IN ('confirmed','edited')
          ORDER BY um.meeting_date DESC, tu.created_at DESC LIMIT 1) u ON true
     WHERE t.task_id = k.task_id
$$;

-- Контекст для промпта: вместо 8 навыков — словарь тем с примерами слов и синонимами
CREATE OR REPLACE FUNCTION app.prompt_context(p_date date)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE
    m   app.calendar%ROWTYPE;
    cur app.sprints%ROWTYPE;
    nxt app.sprints%ROWTYPE;
    res jsonb;
BEGIN
    SELECT * INTO m FROM app.calendar WHERE date = p_date;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Дата встречи % вне производственного календаря', p_date;
    END IF;
    SELECT * INTO cur FROM app.sprints WHERE sprint_id = m.sprint_id;
    SELECT * INTO nxt FROM app.sprints WHERE start_date > p_date ORDER BY start_date LIMIT 1;

    WITH team AS (
        SELECT e.*, n.full_name, n.aliases
        FROM app.employees e JOIN pii.employee_names n USING (employee_id)
        WHERE (e.active_from IS NULL OR e.active_from <= p_date)
          AND (e.active_to   IS NULL OR e.active_to   >= p_date)
    ), days AS (
        SELECT c.date
        FROM app.calendar c
        WHERE c.date BETWEEN m.week_start AND m.week_start + 20
    ), topic_words AS (
        SELECT t.topic_id, t.sort_order, t.name,
               (SELECT coalesce(jsonb_agg(DISTINCT w), '[]'::jsonb) FROM (
                    SELECT unnest(t.speech_examples) AS w
                    UNION SELECT a.alias FROM app.skill_aliases a WHERE a.target = 'topic' AND a.topic_id = t.topic_id) x) AS examples
        FROM app.topics t
    )
    SELECT jsonb_build_object(
        'meeting_date', p_date,
        'meeting', app.day_info(p_date),
        'manager_id', (SELECT employee_id FROM team WHERE is_manager ORDER BY employee_id LIMIT 1),
        'team', (SELECT jsonb_agg(jsonb_build_object(
                    'employee_id', employee_id, 'full_name', full_name, 'aliases', aliases,
                    'position', position, 'is_manager', is_manager) ORDER BY is_manager DESC, employee_id) FROM team),
        'topics', (SELECT jsonb_agg(jsonb_build_object('skill_id', topic_id, 'name', name, 'examples', examples) ORDER BY sort_order)
                   FROM topic_words),
        'open_tasks', (SELECT coalesce(jsonb_agg(jsonb_build_object(
                    'task_id', s.task_ref, 'kind', s.kind, 'what', s.what, 'status', s.status,
                    'owner_ids', coalesce(to_jsonb(s.owner_ids), '[]'::jsonb), 'meeting_date', s.meeting_date,
                    'meeting_employee_id', s.meeting_employee_id,
                    'due', jsonb_build_object('type', s.due_type, 'value', s.due_date, 'value_to', s.due_date_to, 'said', s.due_said))
                    ORDER BY s.meeting_date, s.task_ref), '[]'::jsonb)
                    FROM app.v_open_tasks s WHERE s.meeting_date < p_date),
        'development_paths', (SELECT coalesce(jsonb_agg(jsonb_build_object('path_id', path_id, 'name', name, 'examples', speech_examples) ORDER BY path_id), '[]'::jsonb)
                              FROM app.development_paths),
        'observation_terms', (SELECT coalesce(jsonb_agg(alias ORDER BY id), '[]'::jsonb) FROM app.skill_aliases WHERE target = 'observation'),
        'ignore_terms', (SELECT coalesce(jsonb_agg(jsonb_build_object('alias', alias, 'note', note) ORDER BY id), '[]'::jsonb)
                         FROM app.skill_aliases WHERE target = 'ignore'),
        'weeks', (SELECT jsonb_agg(w ORDER BY wk) FROM (
                    SELECT (date - m.week_start) / 7 AS wk,
                           jsonb_agg(app.day_info(date) ORDER BY date) AS w
                    FROM days GROUP BY 1) s),
        'plus_working_days', (SELECT jsonb_agg(jsonb_build_object('n', n) || app.day_info(app.wd_add(p_date, n)) ORDER BY n) FROM unnest(ARRAY[1,2,3,5,10]) n),
        'month_end', app.day_info((date_trunc('month', p_date) + interval '1 month - 1 day')::date),
        'next_month_end', app.day_info((date_trunc('month', p_date) + interval '2 month - 1 day')::date),
        'current_sprint', CASE WHEN cur.sprint_id IS NULL THEN NULL ELSE jsonb_build_object('sprint_id', cur.sprint_id, 'start', app.day_info(cur.start_date), 'end', app.day_info(cur.end_date)) END,
        'next_sprint',    CASE WHEN nxt.sprint_id IS NULL THEN NULL ELSE jsonb_build_object('sprint_id', nxt.sprint_id, 'start', app.day_info(nxt.start_date), 'end', app.day_info(nxt.end_date)) END,
        'special_days', (SELECT coalesce(jsonb_agg(jsonb_build_object('ru', to_char(date,'DD.MM.YYYY'), 'weekday', weekday, 'name', holiday_name) ORDER BY date), '[]'::jsonb)
                         FROM app.calendar WHERE date > p_date AND date <= p_date + 42 AND holiday_name IS NOT NULL),
        'alias_map', (SELECT coalesce(jsonb_agg(jsonb_build_object('alias', a.alias, 'id', a.employee_id)
                                                ORDER BY length(a.alias) DESC, a.alias), '[]'::jsonb)
                      FROM pii.employee_aliases a JOIN team t USING (employee_id)
                      WHERE a.use_for_pseudonymization)
    ) INTO res;
    RETURN res;
END $$;

-- Сохранение выжимки по контракту schema_version 1.0.
-- p_stage = 'draft'  — ответ модели (блок meeting уже дополнен в n8n): summary_draft, пункты не создаются.
-- p_stage = 'final'  — подтверждено руководителем: summary_final + задачи, события навыков, наблюдения, сигналы.
-- Нарушение контракта — исключение со списком ВСЕХ ошибок; n8n отдаёт его модели при повторе как есть.
CREATE OR REPLACE FUNCTION app.save_summary(p_meeting_id uuid, p jsonb, p_stage text DEFAULT 'final')
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    t         jsonb;
    i         integer := 0;
    v_task    uuid;
    v_due     jsonb;
    v_owner   text;
    v_emp     text;
    v_m_emp   text;
    v_errs    text[] := '{}';
    v_list    text[];
    v_bad     text;
    k         text;
    n_tasks   integer := 0;
    n_skills  integer := 0;
    n_sig     integer := 0;
    n_upd     integer := 0;
    v_touched uuid[];
BEGIN
    IF p_stage NOT IN ('draft','final') THEN
        RAISE EXCEPTION 'p_stage должен быть draft или final';
    END IF;
    SELECT employee_id INTO v_m_emp FROM app.meetings WHERE meeting_id = p_meeting_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Встреча % не найдена', p_meeting_id;
    END IF;
    IF jsonb_typeof(p) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'Нарушен контракт выжимки 1.0: ответ должен быть одним JSON-объектом';
    END IF;

    -- Каркас документа
    IF coalesce(p->>'schema_version','') <> '1.0' THEN
        v_errs := v_errs || format('schema_version должен быть "1.0", получено: %s', coalesce(p->>'schema_version', 'нет поля'));
    END IF;
    FOREACH k IN ARRAY ARRAY['tasks','task_updates','skills','observations','signals'] LOOP
        IF jsonb_typeof(p->k) IS DISTINCT FROM 'array' THEN
            v_errs := v_errs || format('поле %s должно быть массивом (пустым, если пунктов нет)', k);
        END IF;
    END LOOP;
    IF jsonb_typeof(p->'unresolved') IS DISTINCT FROM 'object'
       OR jsonb_typeof(p#>'{unresolved,unknown_names}') IS DISTINCT FROM 'array'
       OR jsonb_typeof(p#>'{unresolved,ambiguous_dates}') IS DISTINCT FROM 'array' THEN
        v_errs := v_errs || 'нужен объект unresolved с массивами unknown_names и ambiguous_dates (пустыми, если нечего указать)';
    END IF;
    IF jsonb_typeof(p->'meeting') IS DISTINCT FROM 'object' THEN
        v_errs := v_errs || 'нужен объект meeting с полем employee_id';
    ELSIF p #>> '{meeting,prompt_version}' IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM app.prompts WHERE prompt_version = p #>> '{meeting,prompt_version}') THEN
        -- это ошибка n8n, а не модели: исправлять повтором бесполезно
        RAISE EXCEPTION 'Версии промпта "%" нет в app.prompts — добавьте её до запуска workflow', p #>> '{meeting,prompt_version}';
    END IF;
    IF cardinality(v_errs) > 0 THEN
        RAISE EXCEPTION 'Нарушен контракт выжимки 1.0: %', array_to_string(v_errs, '; ');
    END IF;

    -- Сотрудник встречи: может быть null, если модель не определила
    v_emp := p #>> '{meeting,employee_id}';
    IF v_emp IS NOT NULL AND NOT EXISTS (SELECT 1 FROM app.employees WHERE employee_id = v_emp AND NOT is_manager) THEN
        v_errs := v_errs || format('meeting.employee_id "%s" нет среди сотрудников команды; если сотрудник не понятен, верни null', v_emp);
    END IF;

    -- ID сотрудников во всех пунктах
    SELECT string_agg(DISTINCT x, ', ') INTO v_bad
    FROM (SELECT jsonb_array_elements_text(e->'owner_ids') x
            FROM jsonb_array_elements(p->'tasks') e WHERE jsonb_typeof(e->'owner_ids') = 'array'
          UNION ALL SELECT e->>'employee_id' FROM jsonb_array_elements(p->'skills') e
          UNION ALL SELECT e->>'employee_id' FROM jsonb_array_elements(p->'observations') e
          UNION ALL SELECT e->>'employee_id' FROM jsonb_array_elements(p->'signals') e) s
    WHERE x IS NOT NULL AND NOT EXISTS (SELECT 1 FROM app.employees WHERE employee_id = x);
    IF v_bad IS NOT NULL THEN
        v_errs := v_errs || format('неизвестные employee_id: %s. Используй только ID из состава команды, остальные имена — в unresolved.unknown_names', v_bad);
    END IF;

    -- Темы навыков
    SELECT string_agg(DISTINCT e->>'skill_id', ', ') INTO v_bad
    FROM jsonb_array_elements(p->'skills') e
    WHERE coalesce(e->>'skill_id','') <> '' AND NOT EXISTS (SELECT 1 FROM app.topics tp WHERE tp.topic_id = e->>'skill_id');
    IF v_bad IS NOT NULL THEN
        v_errs := v_errs || format('неизвестные skill_id: %s. Используй только skill_id из списка тем; то, чего в списке нет, перенеси в observations', v_bad);
    END IF;

    -- Задачи и договорённости
    SELECT array_agg(format('задача %s: %s', n, msg) ORDER BY n, ord) INTO v_list FROM (
        SELECT n, ord, msg FROM jsonb_array_elements(p->'tasks') WITH ORDINALITY x(e, n),
        LATERAL unnest(ARRAY[
            CASE WHEN coalesce(e->>'what','') = '' THEN 'пустое поле what' END,
            CASE WHEN coalesce(e->>'evidence','') = '' THEN 'пустое поле evidence — нужна дословная цитата из транскрипта' END,
            CASE WHEN coalesce(e->>'kind','') NOT IN ('task','agreement')
                 THEN format('kind "%s" не из списка task, agreement', coalesce(e->>'kind','нет поля')) END,
            CASE WHEN jsonb_typeof(e->'owner_ids') IS DISTINCT FROM 'array'
                 THEN 'owner_ids должен быть массивом ID (пустым, если исполнитель не назван)' END,
            app.due_error(e->'due')
        ]) WITH ORDINALITY u(msg, ord)
        WHERE msg IS NOT NULL
    ) s;
    v_errs := v_errs || coalesce(v_list, '{}');

    -- Обновления задач с прошлых встреч
    SELECT array_agg(format('обновление задачи %s: %s', n, msg) ORDER BY n, ord) INTO v_list FROM (
        SELECT n, ord, msg FROM jsonb_array_elements(p->'task_updates') WITH ORDINALITY x(e, n),
        LATERAL unnest(ARRAY[
            CASE WHEN coalesce(e->>'what','') = '' THEN 'пустое поле what' END,
            CASE WHEN coalesce(e->>'evidence','') = '' THEN 'пустое поле evidence — нужна дословная цитата из транскрипта' END,
            CASE WHEN coalesce(e->>'status','') NOT IN ('in_progress','done','cancelled','rescheduled')
                 THEN format('status "%s" не из списка in_progress, done, cancelled, rescheduled', coalesce(e->>'status','нет поля')) END,
            CASE WHEN NOT (e ? 'task_id') THEN 'нет поля task_id (null, если задачи нет в списке открытых)'
                 WHEN e->>'task_id' IS NULL THEN NULL
                 WHEN e->>'task_id' !~ '^T[0-9]+$' THEN format('task_id "%s" — нужен ID из списка открытых задач (T и номер) или null', e->>'task_id')
                 WHEN NOT EXISTS (SELECT 1 FROM app.meeting_tasks mt JOIN app.meetings mm USING (meeting_id)
                                  WHERE 'T' || mt.task_no = e->>'task_id' AND mt.meeting_id <> p_meeting_id
                                    AND mm.status IN ('confirmed','edited'))
                     THEN format('задачи %s нет среди открытых задач; если подходящей задачи нет, верни task_id: null', e->>'task_id') END,
            CASE WHEN e->>'status' = 'rescheduled' THEN app.due_error(e->'due')
                 WHEN jsonb_typeof(e->'due') IS DISTINCT FROM 'null' AND e ? 'due'
                     THEN 'due заполняется только при status = rescheduled, иначе null'
                 WHEN NOT (e ? 'due') THEN 'нет поля due (null, если срок не переносили)' END
        ]) WITH ORDINALITY u(msg, ord)
        WHERE msg IS NOT NULL
    ) s;
    v_errs := v_errs || coalesce(v_list, '{}');

    -- Навыки
    SELECT array_agg(format('навык %s: %s', n, msg) ORDER BY n, ord) INTO v_list FROM (
        SELECT n, ord, msg FROM jsonb_array_elements(p->'skills') WITH ORDINALITY x(e, n),
        LATERAL unnest(ARRAY[
            CASE WHEN coalesce(e->>'employee_id','') = '' THEN 'нет employee_id' END,
            CASE WHEN coalesce(e->>'skill_id','') = '' THEN 'нет skill_id' END,
            CASE WHEN coalesce(e->>'direction','') NOT IN ('up','down','same')
                 THEN format('direction "%s" не из списка same, up, down', coalesce(e->>'direction','null')) END,
            CASE WHEN e->>'assessment' IS NOT NULL AND e->>'assessment' NOT IN ('strong','weak')
                 THEN format('assessment "%s" не из списка strong, weak, null', e->>'assessment')
                 WHEN e->>'assessment' IS NOT NULL AND e->>'direction' IS DISTINCT FROM 'same'
                 THEN 'assessment ставится только при direction = same' END,
            CASE WHEN coalesce(e->>'evidence','') = '' THEN 'пустое поле evidence — нужна дословная цитата из транскрипта' END
        ]) WITH ORDINALITY u(msg, ord)
        WHERE msg IS NOT NULL
    ) s;
    v_errs := v_errs || coalesce(v_list, '{}');

    -- Наблюдения
    SELECT array_agg(format('наблюдение %s: %s', n, msg) ORDER BY n, ord) INTO v_list FROM (
        SELECT n, ord, msg FROM jsonb_array_elements(p->'observations') WITH ORDINALITY x(e, n),
        LATERAL unnest(ARRAY[
            CASE WHEN coalesce(e->>'employee_id','') = '' THEN 'нет employee_id' END,
            CASE WHEN coalesce(e->>'text','') = '' THEN 'пустое поле text' END,
            CASE WHEN e->>'direction' IS NOT NULL AND e->>'direction' NOT IN ('up','down','same')
                 THEN format('direction "%s" не из списка same, up, down, null', e->>'direction') END,
            CASE WHEN e->>'assessment' IS NOT NULL AND e->>'assessment' NOT IN ('strong','weak')
                 THEN format('assessment "%s" не из списка strong, weak, null', e->>'assessment')
                 WHEN e->>'assessment' IS NOT NULL AND e->>'direction' IS DISTINCT FROM 'same'
                 THEN 'assessment ставится только при direction = same' END,
            CASE WHEN coalesce(e->>'evidence','') = '' THEN 'пустое поле evidence — нужна дословная цитата из транскрипта' END
        ]) WITH ORDINALITY u(msg, ord)
        WHERE msg IS NOT NULL
    ) s;
    v_errs := v_errs || coalesce(v_list, '{}');

    -- Сигналы
    SELECT array_agg(format('сигнал %s: %s', n, msg) ORDER BY n, ord) INTO v_list FROM (
        SELECT n, ord, msg FROM jsonb_array_elements(p->'signals') WITH ORDINALITY x(e, n),
        LATERAL unnest(ARRAY[
            CASE WHEN coalesce(e->>'type','') NOT IN ('attrition_risk','burnout','motivation','goal','conflict','mood','probation','achievement','other')
                 THEN format('type "%s" не из списка attrition_risk, burnout, motivation, goal, conflict, mood, probation, achievement, other', coalesce(e->>'type','нет поля')) END,
            CASE WHEN coalesce(e->>'tone','') NOT IN ('risk','positive','neutral')
                 THEN format('tone "%s" не из списка risk, positive, neutral', coalesce(e->>'tone','нет поля')) END,
            CASE WHEN coalesce(e->>'text','') = '' THEN 'пустое поле text' END,
            CASE WHEN coalesce(e->>'evidence','') = '' THEN 'пустое поле evidence — нужна дословная цитата из транскрипта' END
        ]) WITH ORDINALITY u(msg, ord)
        WHERE msg IS NOT NULL
    ) s;
    v_errs := v_errs || coalesce(v_list, '{}');

    IF cardinality(v_errs) > 0 THEN
        RAISE EXCEPTION 'Нарушен контракт выжимки 1.0: %', array_to_string(v_errs, '; ');
    END IF;

    IF p_stage = 'draft' THEN
        UPDATE app.meetings
           SET summary_draft  = coalesce(summary_draft, p),
               employee_id    = coalesce(v_emp, employee_id),
               manager_id     = coalesce(p #>> '{meeting,manager_id}', manager_id),
               prompt_version = coalesce(p #>> '{meeting,prompt_version}', prompt_version),
               status = 'summarized', updated_at = now()
         WHERE meeting_id = p_meeting_id;
        RETURN jsonb_build_object('stage', 'draft', 'meeting_id', p_meeting_id);
    END IF;

    -- final: сотрудник встречи должен быть известен
    IF coalesce(v_emp, v_m_emp) IS NULL THEN
        RAISE EXCEPTION 'Не определён сотрудник встречи: укажите его перед подтверждением выжимки';
    END IF;

    -- пересобираем пункты (повторное подтверждение после правки не плодит дубли)
    -- задачи, которые эта встреча обновляла раньше: после пересборки пересчитаем их статус
    SELECT coalesce(array_agg(DISTINCT task_id), '{}') INTO v_touched
    FROM app.task_updates WHERE meeting_id = p_meeting_id AND task_id IS NOT NULL;
    DELETE FROM app.task_updates    WHERE meeting_id = p_meeting_id;
    DELETE FROM app.meeting_tasks   WHERE meeting_id = p_meeting_id;
    DELETE FROM app.skill_events    WHERE meeting_id = p_meeting_id;
    DELETE FROM app.meeting_signals WHERE meeting_id = p_meeting_id;

    FOR t IN SELECT * FROM jsonb_array_elements(p->'tasks') LOOP
        i := i + 1;
        v_due := t->'due';
        INSERT INTO app.meeting_tasks (meeting_id, item_no, kind, what, due_type, due_date, due_date_to, due_event, due_said, evidence)
        VALUES (p_meeting_id, i, t->>'kind', t->>'what', v_due->>'type',
                (v_due->>'value')::date, (v_due->>'value_to')::date,
                CASE WHEN v_due->>'type' IN ('event','recurring') THEN v_due->>'said' END,
                v_due->>'said', t->>'evidence')
        RETURNING task_id INTO v_task;
        FOR v_owner IN SELECT jsonb_array_elements_text(t->'owner_ids') LOOP
            INSERT INTO app.task_owners VALUES (v_task, v_owner) ON CONFLICT DO NOTHING;
        END LOOP;
        n_tasks := n_tasks + 1;
    END LOOP;

    i := 0;
    FOR t IN SELECT * FROM jsonb_array_elements(p->'task_updates') LOOP
        i := i + 1;
        v_due := CASE WHEN t->>'status' = 'rescheduled' THEN t->'due' END;
        INSERT INTO app.task_updates (meeting_id, item_no, task_id, what, status,
                                      due_type, due_date, due_date_to, due_event, due_said, evidence)
        VALUES (p_meeting_id, i,
                (SELECT mt.task_id FROM app.meeting_tasks mt WHERE 'T' || mt.task_no = t->>'task_id'),
                t->>'what', t->>'status', v_due->>'type', (v_due->>'value')::date, (v_due->>'value_to')::date,
                CASE WHEN v_due->>'type' IN ('event','recurring') THEN v_due->>'said' END,
                v_due->>'said', t->>'evidence');
        n_upd := n_upd + 1;
    END LOOP;

    INSERT INTO app.skill_events (meeting_id, employee_id, topic_id, direction, assessment, evidence)
    SELECT p_meeting_id, e->>'employee_id', e->>'skill_id', e->>'direction', e->>'assessment', e->>'evidence'
    FROM jsonb_array_elements(p->'skills') e;
    GET DIAGNOSTICS n_skills = ROW_COUNT;

    INSERT INTO app.meeting_signals (meeting_id, kind, employee_id, signal_type, direction, assessment, tone, text, evidence)
    SELECT p_meeting_id, 'observation', e->>'employee_id', NULL, e->>'direction', e->>'assessment', NULL, e->>'text', e->>'evidence'
    FROM jsonb_array_elements(p->'observations') e
    UNION ALL
    SELECT p_meeting_id, 'signal', coalesce(e->>'employee_id', v_emp, v_m_emp), e->>'type', NULL, NULL, e->>'tone', e->>'text', e->>'evidence'
    FROM jsonb_array_elements(p->'signals') e;
    GET DIAGNOSTICS n_sig = ROW_COUNT;

    UPDATE app.meetings
       SET summary_final = p,
           employee_id    = coalesce(v_emp, employee_id),
           manager_id     = coalesce(p #>> '{meeting,manager_id}', manager_id),
           prompt_version = coalesce(p #>> '{meeting,prompt_version}', prompt_version),
           status = CASE WHEN summary_draft IS NULL OR summary_draft = p THEN 'confirmed' ELSE 'edited' END,
           confirmed_at = now(), updated_at = now()
     WHERE meeting_id = p_meeting_id;

    -- статусы задач: те, что обновлялись этой встречей раньше, и те, что обновлены сейчас
    PERFORM app.refresh_task(x)
    FROM (SELECT unnest(v_touched) x
          UNION SELECT task_id FROM app.task_updates WHERE meeting_id = p_meeting_id AND task_id IS NOT NULL) z;

    RETURN jsonb_build_object('stage', 'final', 'meeting_id', p_meeting_id,
                              'tasks', n_tasks, 'task_updates', n_upd, 'skills', n_skills, 'observations_and_signals', n_sig);
END $$;

INSERT INTO app.schema_migrations (version, note) VALUES ('007_schema_1_0', 'схема выжимки 1.0: словарь тем, value_to, same/assessment, tone/achievement, task_updates')
ON CONFLICT (version) DO UPDATE SET note = EXCLUDED.note;
