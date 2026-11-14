-- Тестовая замена 02_seed.sql (настоящего файла в проекте нет). Повторяет его поведение:
-- справочники upsert-ом, повторный запуск возвращает старые навыки, синонимы и уровни.

INSERT INTO app.employees (employee_id, position, direction, level, is_manager, active_from, active_to) VALUES
 ('M01','Тимлид QA','{manual}',5,true,NULL,'2026-10-31'),
 ('M02','Тимлид QA','{manual}',5,true,'2026-11-01',NULL),
 ('E01','Младший инженер по тестированию','{manual}',1,false,NULL,NULL),
 ('E02','Старший инженер по нагрузочному тестированию','{load}',4,false,NULL,NULL),
 ('E03','Старший специалист по тестированию','{manual}',3,false,NULL,NULL),
 ('E04','Специалист по тестированию','{manual}',2,false,NULL,NULL),
 ('E05','Инженер по автоматизации','{auto}',3,false,NULL,NULL),
 ('E06','Младший специалист по тестированию','{manual}',1,false,NULL,NULL),
 ('E07','Инженер по автоматизации','{auto}',2,false,NULL,NULL),
 ('E08','Стажёр','{manual}',1,false,NULL,NULL)
ON CONFLICT (employee_id) DO UPDATE SET position = EXCLUDED.position, direction = EXCLUDED.direction,
    is_manager = EXCLUDED.is_manager, active_from = EXCLUDED.active_from, active_to = EXCLUDED.active_to;

INSERT INTO pii.employee_names (employee_id, full_name, first_name, last_name, aliases) VALUES
 ('M01','Татьяна Соколова','Татьяна','Соколова','{Татьяна,я}'),
 ('M02','Александр Белов','Александр','Белов','{Александр,я}'),
 ('E01','Иван Петров','Иван','Петров','{Иван,Ваня,Петров}'),
 ('E02','Павел Смирнов','Павел','Смирнов','{Павел,Паша}'),
 ('E03','Мария Козлова','Мария','Козлова','{Мария,Маша}'),
 ('E04','Сергей Никитин','Сергей','Никитин','{Сергей}'),
 ('E05','Дмитрий Орлов','Дмитрий','Орлов','{Дмитрий,Дима}'),
 ('E06','Елена Васильева','Елена','Васильева','{Елена,Лена}'),
 ('E07','Ольга Морозова','Ольга','Морозова','{Ольга,Оля}'),
 ('E08','Алексей Сидоров','Алексей','Сидоров','{Алексей}')
ON CONFLICT (employee_id) DO NOTHING;

INSERT INTO pii.employee_aliases (alias, alias_lc, employee_id, alias_type, grammatical_case) VALUES
 ('Иваном Петровым','иваном петровым','E01','full_name','ablt'),
 ('Иван','иван','E01','first_name','nomn'),
 ('Павлом','павлом','E02','first_name','ablt')
ON CONFLICT (alias_lc, employee_id) DO NOTHING;

INSERT INTO app.skills (skill_id, matrix_order, name, aliases) VALUES
 ('test_design',1,'Тест-дизайн','{тест-кейсы}'),
 ('func_regress',2,'Функц. и регресс','{регресс}'),
 ('api',3,'API','{Postman}'),
 ('ui_auto',4,'UI-автотесты','{Playwright}'),
 ('python',5,'Python','{pytest}'),
 ('cicd',6,'CI/CD','{пайплайн}'),
 ('load',7,'Нагрузка','{k6}'),
 ('mobile',8,'Мобильное','{iOS,Android}')
ON CONFLICT (skill_id) DO UPDATE SET name = EXCLUDED.name, aliases = EXCLUDED.aliases;

INSERT INTO app.skill_aliases (alias, alias_lc, target, skill_id, note) VALUES
 ('pytest','pytest','skill','python',NULL),
 ('Playwright','playwright','skill','ui_auto',NULL),
 ('плейрайт','плейрайт','skill','ui_auto','транслитерация'),
 ('k6','k6','skill','load',NULL),
 ('регрессионная модель','регрессионная модель','skill','func_regress',NULL),
 ('мерж-реквест','мерж-реквест','skill','cicd',NULL),
 ('iOS','ios','skill','mobile',NULL),
 ('Postman','postman','skill','api',NULL),
 ('тест-кейсы','тест-кейсы','skill','test_design',NULL),
 ('SQL','sql','observation',NULL,'вне матрицы'),
 ('Эскуэль','эскуэль','observation',NULL,'SQL после распознавания'),
 ('баг-репорты','баг-репорты','observation',NULL,'вне матрицы'),
 ('оценка трудозатрат','оценка трудозатрат','observation',NULL,'вне матрицы'),
 ('анализ логов','анализ логов','observation',NULL,'вне матрицы'),
 ('архитектура фреймворка','архитектура фреймворка','observation',NULL,'вне матрицы'),
 ('тестирование требований','тестирование требований','observation',NULL,'вне матрицы'),
 ('презентация','презентация','observation',NULL,'вне матрицы'),
 ('управленческий опыт','управленческий опыт','observation',NULL,'вне матрицы'),
 ('наставничество','наставничество','observation',NULL,'вне матрицы'),
 ('тест-аналитика','тест-аналитика','observation',NULL,'вне матрицы'),
 ('ручное тестирование','ручное тестирование','skill','func_regress',NULL),
 ('функциональное тестирование','функциональное тестирование','skill','func_regress',NULL),
 ('покрытие тестами','покрытие тестами','skill','test_design',NULL),
 ('Allure','allure','ignore',NULL,'инструмент, не навык'),
 ('TestIT','testit','ignore',NULL,'инструмент, не навык'),
 ('Jira','jira','ignore',NULL,'инструмент, не навык'),
 ('автотесты','автотесты','ignore',NULL,'слишком общее')
ON CONFLICT (alias_lc) DO UPDATE SET target = EXCLUDED.target, skill_id = EXCLUDED.skill_id, note = EXCLUDED.note;

INSERT INTO app.skill_levels (employee_id, skill_id, level, as_of, source)
SELECT e, s, 2, DATE '2026-09-01', 'демо-матрица'
FROM unnest(ARRAY['E01','E02','E05']) e, unnest(ARRAY['python','load','ui_auto']) s
ON CONFLICT DO NOTHING;

-- Спринты по 2 недели и календарь 2026–2027 (упрощённый: только выходные, без праздников)
INSERT INTO app.sprints (sprint_id, start_date, end_date)
SELECT 'S' || to_char(d, 'IYYY') || '-' || lpad(n::text, 2, '0'), d, d + 13
FROM (SELECT DATE '2026-01-05' + 14 * k AS d, k + 1 AS n FROM generate_series(0, 51) k) s
ON CONFLICT (sprint_id) DO NOTHING;

INSERT INTO app.calendar (date, weekday_num, weekday, day_type, is_working, work_hours, iso_week, week_start, sprint_id, working_day_index)
SELECT d, extract(isodow FROM d), (ARRAY['пн','вт','ср','чт','пт','сб','вс'])[extract(isodow FROM d)],
       CASE WHEN extract(isodow FROM d) > 5 THEN 'weekend' ELSE 'working' END,
       extract(isodow FROM d) <= 5, CASE WHEN extract(isodow FROM d) > 5 THEN 0 ELSE 8 END,
       to_char(d, 'IYYY-"W"IW'), date_trunc('week', d)::date,
       (SELECT sprint_id FROM app.sprints sp WHERE d BETWEEN sp.start_date AND sp.end_date),
       count(*) FILTER (WHERE extract(isodow FROM d) <= 5) OVER (ORDER BY d)
FROM generate_series(DATE '2026-01-01', DATE '2027-12-31', interval '1 day') g(d)
ON CONFLICT (date) DO NOTHING;
