import copy, json, sys
from jsonschema import Draft202012Validator

schema = json.load(open(sys.argv[1], encoding="utf-8"))
Draft202012Validator.check_schema(schema)
v = Draft202012Validator(schema)
base = schema["examples"][0]


def errs(doc):
    return [e.message for e in v.iter_errors(doc)]


def mut(fn):
    d = copy.deepcopy(base)
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


valid = {
    "пример из схемы": lambda d: None,
    "employee_id = null": setp(["meeting", "employee_id"], None),
    "range с двумя датами": due(type="range", value="2026-09-21", value_to="2026-09-25"),
    "from": due(type="from", value="2026-09-28"),
    "event": due(type="event", said="на следующей встрече"),
    "recurring с границами": due(type="recurring", value="2026-09-28", value_to="2026-12-01", said="раз в неделю"),
    "none без слов о сроке": due(type="none", said=None),
    "исполнитель не назван": setp(["tasks", 0, "owner_ids"], []),
    "руководитель исполнитель": setp(["tasks", 0, "owner_ids"], ["M01"]),
    "навык up без assessment": lambda d: d["skills"].__setitem__(0, {"employee_id": "E02", "skill_id": "load_testing", "direction": "up", "evidence": "вырос"}),
    "навык same без assessment": delp(["skills", 0, "assessment"]),
    "навык same weak": setp(["skills", 0, "assessment"], "weak"),
    "наблюдение без оценки": lambda d: d["observations"].__setitem__(0, {"employee_id": "E02", "text": "Перешёл на новый проект", "direction": None, "evidence": "перешёл"}),
    "сигнал achievement positive": lambda d: d["signals"].__setitem__(0, {"type": "achievement", "tone": "positive", "text": "Первый автотест написан", "evidence": "первый автотест написан"}),
    "сигнал neutral": lambda d: d["signals"].__setitem__(0, {"type": "other", "tone": "neutral", "text": "Отказалась от теста", "evidence": "отказалась проходить психологический тест"}),
    "пустые списки": lambda d: d.update(tasks=[], task_updates=[], skills=[], observations=[], signals=[]),
    "обновление in_progress": lambda d: d["task_updates"][0].update(status="in_progress"),
    "обновление cancelled": lambda d: d["task_updates"][0].update(status="cancelled"),
    "обновление без привязки (task_id null)": setp(["task_updates", 0, "task_id"], None),
    "перенос на событие": setp(["task_updates", 1, "due"], {"type": "event", "value": None, "value_to": None, "said": "к следующей встрече"}),
}
invalid = {
    "дата ДД.ММ.ГГГГ": due(type="date", value="25.09.2026"),
    "date без value": due(type="date"),
    "date с value_to": due(type="date", value="2026-09-25", value_to="2026-09-26"),
    "range без value_to": due(type="range", value="2026-09-21"),
    "none с датой": due(type="none", value="2026-09-25"),
    "event без said": due(type="event", said=None),
    "recurring без said": due(type="recurring", said=None),
    "неизвестный due.type": due(type="soon"),
    "нет value_to в due": delp(["tasks", 0, "due", "value_to"]),
    "исполнитель HR": setp(["tasks", 0, "owner_ids"], ["HR"]),
    "kind = plan": setp(["tasks", 0, "kind"], "plan"),
    "нет evidence": delp(["tasks", 0, "evidence"]),
    "direction = confirm": setp(["skills", 0, "direction"], "confirm"),
    "навык без direction": delp(["skills", 0, "direction"]),
    "up с assessment": lambda d: d["skills"][0].update(direction="up", assessment="strong"),
    "наблюдение null с assessment": lambda d: d["observations"][0].update(direction=None, assessment="weak"),
    "навык с direction null": setp(["skills", 0, "direction"], None),
    "сигнал типа health": setp(["signals", 0, "type"], "health"),
    "сигнал без tone": delp(["signals", 0, "tone"]),
    "tone = sad": setp(["signals", 0, "tone"], "sad"),
    "лишнее поле": lambda d: d.update(comment="x"),
    "schema_version 1.1": setp(["schema_version"], "1.1"),
    "нет unresolved": delp(["unresolved"]),
    "сотрудник встречи — руководитель": setp(["meeting", "employee_id"], "M01"),
    "нет task_updates": delp(["task_updates"]),
    "статус open": setp(["task_updates", 0, "status"], "open"),
    "task_id — uuid": setp(["task_updates", 0, "task_id"], "5f1c0a3e-1111-2222-3333-444455556666"),
    "done с новым сроком": setp(["task_updates", 0, "due"], {"type": "date", "value": "2026-10-16", "value_to": None, "said": "x"}),
    "rescheduled без срока": setp(["task_updates", 1, "due"], None),
    "обновление без evidence": delp(["task_updates", 0, "evidence"]),
    "обновление с owner_ids": lambda d: d["task_updates"][0].update(owner_ids=["E02"]),
}

bad = 0
for name, fn in valid.items():
    e = errs(mut(fn))
    if e:
        bad += 1
        print("ОШИБКА: должен пройти —", name, e)
for name, fn in invalid.items():
    e = errs(mut(fn))
    if not e:
        bad += 1
        print("ОШИБКА: должен быть отклонён —", name)
print(f"валидных {len(valid)}, невалидных {len(invalid)}, расхождений {bad}")
