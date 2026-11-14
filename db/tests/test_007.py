"""Проверка миграции 007 на тестовой базе.

1. Согласованность с JSON-схемой: всё, что схема принимает, база принимает; всё, что схема отклоняет, база отклоняет.
2. Проверки, которые есть только в базе: справочники, существующая дата, сотрудник встречи.
3. Подтверждение: что легло в таблицы, просрочка диапазона, события по темам.
4. Контекст для промпта: темы вместо навыков.
"""
import copy
import json
import subprocess
import sys

from jsonschema import Draft202012Validator

import os
PSQL = ["psql", "-d", os.environ.get("PGDATABASE", "assistant_test"), "-At", "-q", "-v", "ON_ERROR_STOP=1"]  # хост, порт, пользователь — PGHOST, PGPORT, PGUSER
schema = json.load(open(sys.argv[1], encoding="utf-8"))
V = Draft202012Validator(schema)
BASE = copy.deepcopy(schema["examples"][0])
FAILS = []


def sql(q):
    r = subprocess.run(PSQL + ["-c", q], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def new_meeting(msg_id):
    return sql(f"INSERT INTO app.meetings (tg_chat_id, tg_message_id, meeting_date, status) "
               f"VALUES (1, {msg_id}, '2026-09-15', 'transcribed') RETURNING meeting_id")


def save(mid, doc, stage="draft"):
    j = json.dumps(doc, ensure_ascii=False)
    try:
        sql(f"SELECT app.save_summary('{mid}', $j${j}$j$::jsonb, '{stage}')")
        return "OK"
    except RuntimeError as e:
        return str(e).replace("ERROR:  ", "")


def mut(fn):
    d = copy.deepcopy(BASE)
    fn(d)
    return d


def setp(path, val):
    def f(d):
        cur = d
        for p in path[:-1]:
            cur = cur[p]
        cur[path[-1]] = val
    return f


def delp(path):
    def f(d):
        cur = d
        for p in path[:-1]:
            cur = cur[p]
        del cur[path[-1]]
    return f


def due(**kw):
    def f(d):
        d["tasks"][0]["due"] = {"type": "date", "value": None, "value_to": None, "said": "x", **kw}
    return f


def check(name, ok, detail=""):
    print(("ок  " if ok else "ОШИБКА ") + name + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


# ---------- 0. прошлая встреча с открытыми задачами (на них ссылается task_updates) ----------
PREV = new_meeting(900)
prev_doc = copy.deepcopy(BASE)
prev_doc.update(task_updates=[], skills=[], observations=[], signals=[])
sql(f"SELECT app.save_summary('{PREV}', $j${json.dumps(prev_doc, ensure_ascii=False)}$j$::jsonb, 'final')")
REFS = sql(f"SELECT string_agg('T' || task_no, ',' ORDER BY item_no) FROM app.meeting_tasks WHERE meeting_id = '{PREV}'").split(",")
BASE["task_updates"][0]["task_id"] = REFS[0]
BASE["task_updates"][1]["task_id"] = REFS[1]

# ---------- 1. согласованность со схемой ----------
schema_cases = {
    "пример из схемы": (lambda d: None, True),
    "employee_id = null": (setp(["meeting", "employee_id"], None), True),
    "range": (due(type="range", value="2026-09-21", value_to="2026-09-25"), True),
    "from": (due(type="from", value="2026-09-28"), True),
    "event": (due(type="event", said="на следующей встрече"), True),
    "recurring с границами": (due(type="recurring", value="2026-09-28", value_to="2026-12-01", said="раз в неделю"), True),
    "none без слов": (due(type="none", said=None), True),
    "исполнитель не назван": (setp(["tasks", 0, "owner_ids"], []), True),
    "руководитель — исполнитель": (setp(["tasks", 0, "owner_ids"], ["M01"]), True),
    "навык same weak": (setp(["skills", 0, "assessment"], "weak"), True),
    "навык same без assessment": (delp(["skills", 0, "assessment"]), True),
    "наблюдение без оценки": (lambda d: d["observations"].__setitem__(0, {"employee_id": "E02", "text": "Перешёл на новый проект", "direction": None, "evidence": "перешёл"}), True),
    "сигнал achievement": (lambda d: d["signals"].__setitem__(0, {"type": "achievement", "tone": "positive", "text": "Первый автотест", "evidence": "первый автотест написан"}), True),
    "сигнал neutral": (lambda d: d["signals"].__setitem__(0, {"type": "other", "tone": "neutral", "text": "Отказалась от теста", "evidence": "отказалась проходить психологический тест"}), True),
    "пустые списки": (lambda d: d.update(tasks=[], skills=[], observations=[], signals=[]), True),
    "дата ДД.ММ.ГГГГ": (due(type="date", value="25.09.2026"), False),
    "date без value": (due(type="date"), False),
    "date с value_to": (due(type="date", value="2026-09-25", value_to="2026-09-26"), False),
    "range без value_to": (due(type="range", value="2026-09-21"), False),
    "range массивом (старый формат)": (lambda d: d["tasks"][0].update(due={"type": "range", "value": ["2026-09-21", "2026-09-25"], "value_to": None, "said": "x"}), False),
    "none с датой": (due(type="none", value="2026-09-25"), False),
    "event без said": (due(type="event", said=None), False),
    "recurring без said": (due(type="recurring", said=None), False),
    "неизвестный due.type": (due(type="soon"), False),
    "kind = plan": (setp(["tasks", 0, "kind"], "plan"), False),
    "нет evidence": (delp(["tasks", 0, "evidence"]), False),
    "direction = confirm": (setp(["skills", 0, "direction"], "confirm"), False),
    "up с assessment": (lambda d: d["skills"][0].update(direction="up", assessment="strong"), False),
    "сигнал типа health": (setp(["signals", 0, "type"], "health"), False),
    "сигнал без tone": (delp(["signals", 0, "tone"]), False),
    "нет unresolved": (delp(["unresolved"]), False),
    "tasks не массив": (setp(["tasks"], {}), False),
    "обновление in_progress": (lambda d: d["task_updates"][0].update(status="in_progress"), True),
    "обновление cancelled": (lambda d: d["task_updates"][0].update(status="cancelled"), True),
    "обновление без привязки": (setp(["task_updates", 0, "task_id"], None), True),
    "перенос на событие": (setp(["task_updates", 1, "due"], {"type": "event", "value": None, "value_to": None, "said": "к следующей встрече"}), True),
    "нет task_updates": (delp(["task_updates"]), False),
    "статус open": (setp(["task_updates", 0, "status"], "open"), False),
    "task_id — uuid": (setp(["task_updates", 0, "task_id"], "5f1c0a3e-1111-2222-3333-444455556666"), False),
    "done с новым сроком": (setp(["task_updates", 0, "due"], {"type": "date", "value": "2026-10-16", "value_to": None, "said": "x"}), False),
    "rescheduled без срока": (setp(["task_updates", 1, "due"], None), False),
    "перенос на ДД.ММ.ГГГГ": (setp(["task_updates", 1, "due"], {"type": "date", "value": "16.10.2026", "value_to": None, "said": "x"}), False),
    "обновление без evidence": (delp(["task_updates", 0, "evidence"]), False),
}
n = 1000
for name, (fn, expect_ok) in schema_cases.items():
    doc = mut(fn)
    s_ok = not list(V.iter_errors(doc))
    n += 1
    db = save(new_meeting(n), doc)
    check(f"[схема={'да' if s_ok else 'нет'}] {name}", s_ok == expect_ok and (db == "OK") == expect_ok, f"схема {s_ok}, база: {db}")

# ---------- 2. только база ----------
db_only = {
    "исполнитель E99": setp(["tasks", 0, "owner_ids"], ["E99"]),
    "неизвестная тема load": setp(["skills", 0, "skill_id"], "load"),
    "несуществующая дата 2026-02-30": due(type="date", value="2026-02-30"),
    "range наоборот": due(type="range", value="2026-09-25", value_to="2026-09-21"),
    "встреча с руководителем M01": setp(["meeting", "employee_id"], "M01"),
    "обновление несуществующей задачи T99999": setp(["task_updates", 0, "task_id"], "T99999"),
}
for name, fn in db_only.items():
    n += 1
    db = save(new_meeting(n), mut(fn))
    check(f"[база] отклоняет: {name}", db != "OK", db)

# все ошибки одним сообщением
n += 1
many = mut(lambda d: (d["tasks"][0].update(kind="plan", evidence=""), d["skills"][0].update(skill_id="sql"), d["signals"][0].update(tone="sad")))
msg = save(new_meeting(n), many)
check("[база] все ошибки в одном сообщении", all(x in msg for x in ("kind", "evidence", "sql", "tone")), msg)
print("   пример текста ошибки для модели:", msg[:400])

# ---------- 3. подтверждение ----------
n += 1
mid = new_meeting(n)
final_doc = mut(lambda d: (
    d["tasks"].append({"kind": "task", "owner_ids": ["E02", "E05"], "what": "Встреча втроём", "due": {"type": "range", "value": "2026-09-21", "value_to": "2026-09-25", "said": "на следующей неделе"}, "evidence": "на следующей неделе соберёмся"}),
    d["tasks"].append({"kind": "task", "owner_ids": ["M01"], "what": "Обсудить с Павлом", "due": {"type": "event", "value": None, "value_to": None, "said": "на следующей встрече"}, "evidence": "на следующей встрече обсудим"}),
    d["signals"].append({"type": "achievement", "tone": "positive", "text": "Профиль готов", "evidence": "профиль готов"}),
    d.update(task_updates=[]),
))
check("черновик сохраняется", save(mid, final_doc, "draft") == "OK")
check("подтверждение сохраняется", save(mid, final_doc, "final") == "OK")
check("повторное подтверждение без дублей", save(mid, final_doc, "final") == "OK"
      and sql(f"SELECT count(*) FROM app.meeting_tasks WHERE meeting_id = '{mid}'") == "5")
rows = sql(f"SELECT item_no || ':' || due_type || ':' || coalesce(due_date::text,'-') || ':' || coalesce(due_date_to::text,'-') || ':' || coalesce(due_event,'-') "
           f"FROM app.meeting_tasks WHERE meeting_id = '{mid}' ORDER BY item_no")
print("   задачи:", rows.replace("\n", " | "))
check("recurring: периодичность в due_event", ":recurring:-:-:раз в неделю по часу" in rows)
check("range: две даты", "4:range:2026-09-21:2026-09-25" in rows)
check("event: слова в due_event", "5:event:-:-:на следующей встрече" in rows)
check("исполнители на двоих", sql(f"SELECT count(*) FROM app.task_owners o JOIN app.meeting_tasks t USING (task_id) WHERE t.meeting_id = '{mid}' AND t.item_no = 4") == "2")
check("событие навыка с темой и оценкой", sql(f"SELECT topic_id || ':' || direction || ':' || assessment FROM app.skill_events WHERE meeting_id = '{mid}'") == "load_testing:same:strong")
check("сигнал с tone", sql(f"SELECT count(*) FROM app.meeting_signals WHERE meeting_id = '{mid}' AND kind = 'signal' AND tone = 'positive' AND signal_type = 'achievement'") == "1")
check("статус confirmed", sql(f"SELECT status FROM app.meetings WHERE meeting_id = '{mid}'") == "confirmed")
check("просрочка диапазона", sql(f"SELECT overdue FROM app.v_open_tasks WHERE task_id IN (SELECT task_id FROM app.meeting_tasks WHERE meeting_id = '{mid}' AND item_no = 4)") == "t")
check("recurring не просрочен", sql(f"SELECT overdue FROM app.v_open_tasks WHERE task_id IN (SELECT task_id FROM app.meeting_tasks WHERE meeting_id = '{mid}' AND item_no = 3)") == "f")
check("v_topic_events видит событие", sql("SELECT same_strong FROM app.v_topic_events WHERE employee_id = 'E02' AND topic_id = 'load_testing'") == "1")

n += 1
mid2 = new_meeting(n)
nobody = mut(setp(["meeting", "employee_id"], None))
save(mid2, nobody, "draft")
check("подтверждение без сотрудника встречи отклоняется", "Не определён сотрудник" in save(mid2, nobody, "final"))

# ---------- 3а. обновления задач ----------
def task_row(ref):
    return sql(f"SELECT status || ':' || due_type || ':' || coalesce(due_date::text,'-') || ':' || rescheduled "
               f"FROM app.v_task_state WHERE task_ref = '{ref}'")

check("до обновлений задачи открыты", task_row(REFS[0]).startswith("open:") and task_row(REFS[1]) == "open:none:-:false", task_row(REFS[1]))
n += 1
upd = new_meeting(n)
sql(f"UPDATE app.meetings SET meeting_date = '2026-09-25' WHERE meeting_id = '{upd}'")
upd_doc = copy.deepcopy(BASE)
upd_doc.update(tasks=[], skills=[], observations=[], signals=[])
check("черновик с обновлениями не меняет задачи", save(upd, upd_doc, "draft") == "OK" and task_row(REFS[0]).startswith("open:"))
check("подтверждение с обновлениями", save(upd, upd_doc, "final") == "OK")
check("done: задача закрыта", task_row(REFS[0]).startswith("done:"), task_row(REFS[0]))
check("rescheduled: новый срок, статус прежний", task_row(REFS[1]) == "open:date:2026-10-16:true", task_row(REFS[1]))
check("закрытая задача ушла из открытых", sql(f"SELECT count(*) FROM app.v_open_tasks WHERE task_ref = '{REFS[0]}'") == "0")
ctx_after = json.loads(sql("SELECT app.prompt_context('2026-10-01')"))
ot = {t["task_id"]: t for t in ctx_after["open_tasks"]}
check("контекст: открытые задачи с ID и новым сроком", REFS[0] not in ot and ot.get(REFS[1], {}).get("due", {}).get("value") == "2026-10-16", ot.get(REFS[1]))
upd_doc["task_updates"][0]["status"] = "in_progress"
upd_doc["task_updates"] = upd_doc["task_updates"][:1]
check("правка встречи пересчитывает статусы", save(upd, upd_doc, "final") == "OK"
      and task_row(REFS[0]).startswith("in_progress:") and task_row(REFS[1]) == "open:none:-:false", task_row(REFS[1]))
upd_doc["task_updates"][0]["task_id"] = None
check("непривязанное обновление сохраняется без задачи", save(upd, upd_doc, "final") == "OK"
      and sql(f"SELECT count(*) FROM app.task_updates WHERE meeting_id = '{upd}' AND task_id IS NULL") == "1"
      and task_row(REFS[0]).startswith("open:"))
self_ref = sql(f"SELECT 'T' || task_no FROM app.meeting_tasks WHERE meeting_id = '{PREV}' ORDER BY item_no LIMIT 1")
own = copy.deepcopy(prev_doc); own["task_updates"] = [{"task_id": self_ref, "what": "x", "status": "done", "due": None, "evidence": "x"}]
check("нельзя обновить задачу своей же встречи", "нет среди открытых" in save(PREV, own, "final"))

# ---------- 4. контекст ----------
ctx = json.loads(sql("SELECT app.prompt_context('2026-09-15')"))
pseudo = json.loads(sql("SELECT app.prompt_context_pseudo('2026-09-15')"))
check("в контексте 43 темы и нет старых навыков", len(ctx["topics"]) == 43 and "skills" not in ctx)
pyt = next(t for t in ctx["topics"] if t["skill_id"] == "programming")
check("синоним pytest попал в примеры темы", "pytest" in pyt["examples"], pyt)
check("контекст без ФИО тоже с темами", len(pseudo["topics"]) == 43 and "alias_map" not in pseudo)

print("\nИтого ошибок:", len(FAILS))
sys.exit(1 if FAILS else 0)
