-- Функции и представления · задача e03

-- Дата через N рабочих дней (N может быть отрицательным). Для выходного отсчёт идёт от предыдущего рабочего дня.
CREATE OR REPLACE FUNCTION app.wd_add(p_date date, p_n integer)
RETURNS date LANGUAGE sql STABLE AS $$
    SELECT c2.date
    FROM app.calendar c1
    JOIN app.calendar c2 ON c2.is_working AND c2.working_day_index = c1.working_day_index + p_n
    WHERE c1.date = p_date
$$;

-- Описание дня для промпта: дата, ДД.ММ.ГГГГ, день недели, рабочий ли
CREATE OR REPLACE FUNCTION app.day_info(p_date date)
RETURNS jsonb LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object('date', c.date, 'ru', to_char(c.date, 'DD.MM.YYYY'), 'weekday', c.weekday,
                              'is_working', c.is_working, 'holiday', c.holiday_name)
    FROM app.calendar c WHERE c.date = p_date
$$;

-- Контекст для промпта на дату встречи: команда, навыки, опорные даты, карта псевдонимизации.
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
    )
    SELECT jsonb_build_object(
        'meeting_date', p_date,
        'meeting', app.day_info(p_date),
        'manager_id', (SELECT employee_id FROM team WHERE is_manager ORDER BY employee_id LIMIT 1),
        'team', (SELECT jsonb_agg(jsonb_build_object(
                    'employee_id', employee_id, 'full_name', full_name, 'aliases', aliases,
                    'position', position, 'is_manager', is_manager) ORDER BY is_manager DESC, employee_id) FROM team),
        'skills', (SELECT jsonb_agg(jsonb_build_object('skill_id', skill_id, 'name', name, 'aliases', aliases) ORDER BY matrix_order)
                   FROM app.skills),
        'observation_terms', (SELECT jsonb_agg(alias ORDER BY id) FROM app.skill_aliases WHERE target = 'observation'),
        'ignore_terms', (SELECT jsonb_agg(jsonb_build_object('alias', alias, 'note', note) ORDER BY id)
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

-- Сохранение выжимки по JSON-контракту schema_version 1.0 (раздел 4 «Шаг 2.2»).
-- p_stage = 'draft'  — ответ модели: пишется в summary_draft (один раз), пункты не создаются.
-- p_stage = 'final'  — подтверждено руководителем: summary_final + пункты (задачи, навыки, наблюдения, сигналы).
-- Любое нарушение контракта (неизвестный сотрудник или навык, неверный тип срока) — исключение,
-- n8n ловит его и повторяет запрос к модели с текстом ошибки.
CREATE OR REPLACE FUNCTION app.save_summary(p_meeting_id uuid, p jsonb, p_stage text DEFAULT 'final')
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    t        jsonb;
    i        integer := 0;
    v_task   uuid;
    v_due    jsonb;
    v_from   date;
    v_to     date;
    v_owner  text;
    v_emp    text := p #>> '{meeting,employee_id}';
    v_bad    text;
    n_tasks  integer := 0;
    n_skills integer := 0;
    n_sig    integer := 0;
BEGIN
    IF p_stage NOT IN ('draft','final') THEN
        RAISE EXCEPTION 'p_stage должен быть draft или final';
    END IF;
    IF coalesce(p->>'schema_version','') <> '1.0' THEN
        RAISE EXCEPTION 'schema_version должен быть "1.0", получено: %', p->>'schema_version';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM app.meetings WHERE meeting_id = p_meeting_id) THEN
        RAISE EXCEPTION 'Встреча % не найдена', p_meeting_id;
    END IF;
    IF v_emp IS NOT NULL AND NOT EXISTS (SELECT 1 FROM app.employees WHERE employee_id = v_emp) THEN
        RAISE EXCEPTION 'meeting.employee_id "%" нет в составе команды', v_emp;
    END IF;

    -- Проверки контракта до записи: сначала все ID, чтобы ошибка была понятной
    SELECT string_agg(DISTINCT x, ', ') INTO v_bad
    FROM (SELECT jsonb_array_elements_text(coalesce(e->'owner_ids','[]'::jsonb)) x FROM jsonb_array_elements(coalesce(p->'tasks','[]'::jsonb)) e
          UNION ALL SELECT e->>'employee_id' FROM jsonb_array_elements(coalesce(p->'skills','[]'::jsonb)) e
          UNION ALL SELECT e->>'employee_id' FROM jsonb_array_elements(coalesce(p->'observations','[]'::jsonb)) e
          UNION ALL SELECT e->>'employee_id' FROM jsonb_array_elements(coalesce(p->'signals','[]'::jsonb)) e WHERE e ? 'employee_id') s
    WHERE x IS NOT NULL AND NOT EXISTS (SELECT 1 FROM app.employees WHERE employee_id = x);
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'Неизвестные employee_id: %. Используй только ID из состава команды, остальные имена — в unresolved.unknown_names', v_bad;
    END IF;
    SELECT string_agg(DISTINCT e->>'skill_id', ', ') INTO v_bad
    FROM jsonb_array_elements(coalesce(p->'skills','[]'::jsonb)) e
    WHERE NOT EXISTS (SELECT 1 FROM app.skills s WHERE s.skill_id = e->>'skill_id');
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'Неизвестные skill_id: %. Навык вне матрицы перенеси в observations', v_bad;
    END IF;

    SELECT string_agg(format('задача %s: %s', n, msg), '; ') INTO v_bad FROM (
        SELECT n, CASE
            WHEN coalesce(e->>'what','') = '' THEN 'пустое поле what'
            WHEN coalesce(e->>'kind','task') NOT IN ('task','agreement') THEN format('kind "%s" не из списка task, agreement', e->>'kind')
            WHEN coalesce(e#>>'{due,type}','none') NOT IN ('date','range','from','event','recurring','none')
                THEN format('due.type "%s" не из списка date, range, from, event, recurring, none', e#>>'{due,type}')
            WHEN e#>>'{due,type}' IN ('date','from')
                 AND coalesce(e#>>'{due,value}','') !~ '^\d{4}-\d{2}-\d{2}$'
                THEN format('due.value "%s" должен быть датой YYYY-MM-DD', e#>>'{due,value}')
            WHEN e#>>'{due,type}' = 'range'
                 AND NOT (jsonb_typeof(e#>'{due,value}') = 'array'
                          AND coalesce(e#>>'{due,value,0}','') ~ '^\d{4}-\d{2}-\d{2}$'
                          AND coalesce(e#>>'{due,value,1}','') ~ '^\d{4}-\d{2}-\d{2}$')
                THEN 'для range due.value — массив из двух дат YYYY-MM-DD'
            WHEN jsonb_typeof(coalesce(e->'owner_ids','[]'::jsonb)) <> 'array'
                THEN 'owner_ids должен быть массивом ID (пустой, если исполнитель не назван)'
        END AS msg
        FROM jsonb_array_elements(coalesce(p->'tasks','[]'::jsonb)) WITH ORDINALITY x(e, n)
    ) s WHERE msg IS NOT NULL;
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'Нарушен контракт выжимки: %', v_bad;
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

    -- final: пересобираем пункты (повторное подтверждение после правки не плодит дубли)
    DELETE FROM app.meeting_tasks   WHERE meeting_id = p_meeting_id;
    DELETE FROM app.skill_events    WHERE meeting_id = p_meeting_id;
    DELETE FROM app.meeting_signals WHERE meeting_id = p_meeting_id;

    FOR t IN SELECT * FROM jsonb_array_elements(coalesce(p->'tasks','[]'::jsonb)) LOOP
        i := i + 1;
        v_due := coalesce(t->'due', '{"type":"none"}'::jsonb);
        v_from := NULL; v_to := NULL;
        IF jsonb_typeof(v_due->'value') = 'array' THEN
            v_from := (v_due->'value'->>0)::date;
            v_to   := (v_due->'value'->>1)::date;
        ELSIF v_due->>'type' IN ('date','from') THEN
            v_from := (v_due->>'value')::date;
        END IF;
        INSERT INTO app.meeting_tasks (meeting_id, item_no, kind, what, due_type, due_date, due_date_to, due_event, due_said, evidence)
        VALUES (p_meeting_id, i, coalesce(t->>'kind','task'), t->>'what', coalesce(v_due->>'type','none'),
                v_from, v_to,
                CASE WHEN v_due->>'type' IN ('event','recurring') THEN v_due->>'value' END,
                v_due->>'said', t->>'evidence')
        RETURNING task_id INTO v_task;
        FOR v_owner IN SELECT jsonb_array_elements_text(coalesce(t->'owner_ids','[]'::jsonb)) LOOP
            INSERT INTO app.task_owners VALUES (v_task, v_owner) ON CONFLICT DO NOTHING;
        END LOOP;
        n_tasks := n_tasks + 1;
    END LOOP;

    INSERT INTO app.skill_events (meeting_id, employee_id, skill_id, direction, evidence)
    SELECT p_meeting_id, coalesce(e->>'employee_id', v_emp), e->>'skill_id', e->>'direction', e->>'evidence'
    FROM jsonb_array_elements(coalesce(p->'skills','[]'::jsonb)) e;
    GET DIAGNOSTICS n_skills = ROW_COUNT;

    INSERT INTO app.meeting_signals (meeting_id, kind, employee_id, signal_type, direction, text, evidence)
    SELECT p_meeting_id, 'observation', coalesce(e->>'employee_id', v_emp), NULL, e->>'direction', e->>'text', e->>'evidence'
    FROM jsonb_array_elements(coalesce(p->'observations','[]'::jsonb)) e
    UNION ALL
    SELECT p_meeting_id, 'signal', coalesce(e->>'employee_id', v_emp), e->>'type', NULL, e->>'text', e->>'evidence'
    FROM jsonb_array_elements(coalesce(p->'signals','[]'::jsonb)) e;
    GET DIAGNOSTICS n_sig = ROW_COUNT;

    UPDATE app.meetings
       SET summary_final = p,
           employee_id    = coalesce(v_emp, employee_id),
           manager_id     = coalesce(p #>> '{meeting,manager_id}', manager_id),
           prompt_version = coalesce(p #>> '{meeting,prompt_version}', prompt_version),
           status = CASE WHEN summary_draft IS NULL OR summary_draft = p THEN 'confirmed' ELSE 'edited' END,
           confirmed_at = now(), updated_at = now()
     WHERE meeting_id = p_meeting_id;

    RETURN jsonb_build_object('stage', 'final', 'meeting_id', p_meeting_id,
                              'tasks', n_tasks, 'skills', n_skills, 'observations_and_signals', n_sig);
END $$;

-- D07 · открытые задачи и договорённости по сотруднику (для /brief и контекста следующей встречи)
DROP VIEW IF EXISTS app.v_open_tasks;
CREATE VIEW app.v_open_tasks AS
SELECT m.employee_id AS meeting_employee_id, m.meeting_date, t.task_id, t.kind, t.what,
       t.due_type, t.due_date, t.due_date_to, t.due_event, t.due_said,
       array_agg(o.employee_id ORDER BY o.employee_id) AS owner_ids,
       (t.due_type = 'date' AND t.due_date < current_date) AS overdue
FROM app.meeting_tasks t
JOIN app.meetings m USING (meeting_id)
LEFT JOIN app.task_owners o USING (task_id)
WHERE t.status = 'open' AND m.status IN ('confirmed','edited')
GROUP BY m.employee_id, m.meeting_date, t.task_id;

-- D06 + D13 · матрица: последний снимок уровня + сигналы из подтверждённых встреч после него
DROP VIEW IF EXISTS app.v_skill_matrix;
CREATE VIEW app.v_skill_matrix AS
WITH last_level AS (
    SELECT DISTINCT ON (employee_id, skill_id) employee_id, skill_id, level, as_of
    FROM app.skill_levels ORDER BY employee_id, skill_id, as_of DESC
)
SELECT e.employee_id, s.skill_id, s.name AS skill_name, l.level, l.as_of,
       count(*) FILTER (WHERE ev.direction = 'up')   AS ups_since,
       count(*) FILTER (WHERE ev.direction = 'down') AS downs_since,
       max(ev.meeting_date) AS last_event_date
FROM app.employees e
CROSS JOIN app.skills s
LEFT JOIN last_level l USING (employee_id, skill_id)
LEFT JOIN (SELECT ev.employee_id, ev.skill_id, ev.direction, m.meeting_date
           FROM app.skill_events ev JOIN app.meetings m USING (meeting_id)
           WHERE m.status IN ('confirmed','edited')) ev
       ON ev.employee_id = e.employee_id AND ev.skill_id = s.skill_id
      AND (l.as_of IS NULL OR ev.meeting_date >= l.as_of)
WHERE NOT e.is_manager
GROUP BY e.employee_id, s.skill_id, s.name, s.matrix_order, l.level, l.as_of;

-- D17 · сводка по запускам (app.v_runs_daily) создаётся в 04_v5_bot.sql

INSERT INTO app.schema_migrations (version, note) VALUES ('003_functions', 'wd_add, day_info, prompt_context, save_summary, представления')
ON CONFLICT (version) DO NOTHING;
