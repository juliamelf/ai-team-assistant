"""Переразметка оценочного набора v0 по схеме выжимки 1.0 (13.11.2026).

Запуск:  python3 remark_v0.py исходный.xlsx результат.xlsx
Исходный файл не меняется. Меняются:
  Эталон    — группы навыков и наблюдений, новые колонки: тема, направление, оценка, тип и тон сигнала, тип срока;
  Разметка  — навыки и наблюдения v3 сопоставлены с темами; лишние пункты бота пересобраны;
  Правила, Типы ошибок, Проверки, Метрики, Журнал — под схему 1.0.
"""
import copy
import sys

from openpyxl import load_workbook

SKILL = "Навыки (темы)"
OBS = "Наблюдения"

# ID: (тема, направление, оценка, обязательность или None, новая формулировка)
SKILLS = {
    "01-O1": ("bug_reports", "same", "strong", None, "Баг-репорты подробные, эталон для команды"),
    "01-O2": ("estimation", "same", "weak", None, "Оценка трудозатрат: опять ошибся почти в 2 раза"),
    "02-S1": ("load_testing", "same", "strong", None, "Нагрузка — сильнейшая экспертиза в команде"),
    "02-O1": ("autotest_architecture", "same", "strong", None, "Архитектура автотестов — сильнейшая экспертиза в команде"),
    "03-S1": ("test_design", "same", "strong", None, "Тест-дизайн — лучший в команде"),
    "03-S2": ("regression_model", "same", "strong", None, "Регрессионная модель — лучшая в команде"),
    "03-O1": ("team_process", "same", "weak", None, "Управленческого опыта пока нет"),
    "04-S1": ("mobile", "same", "strong", None, "Мобильное: единственный, кто глубоко знает iOS и Android"),
    "04-O1": ("bug_localization", "same", "strong", None, "Логи разбирает отлично"),
    "05-S1": ("programming", "same", "strong", None, "Python (pytest) — уверенно"),
    "05-S2": ("ui_auto", "same", "strong", None, "UI-автотесты (Playwright) — уверенно"),
    "05-S3": ("cicd", "same", "strong", None, "CI в GitLab настраивает сам"),
    "05-S4": ("load_testing", "same", "weak", None, "Нагрузку нужно освоить (для роли ведущего)"),
    "05-O1": ("presentation", "same", "strong", None, "Хорошо презентует на демо спринта"),
    "05-O2": ("autotest_architecture", "same", "weak", None, "Архитектуру фреймворка нужно освоить (для роли ведущего)"),
    "06-O1": ("requirements", "same", "strong", None, "Тестирование требований: дважды нашла противоречия до разработки"),
    "07-S1": ("cicd", "up", None, None, "CI/CD: прогресс по ИПР — API-тесты на каждый мерж-реквест"),
    "07-S2": ("api", "up", None, None, "API-тесты добавила сама"),
    "07-O1": ("test_data_sql", "same", "strong", None, "SQL очень сильный, может помогать Алексею"),
    "08-O1": ("bug_reports", "same", "weak", None, "Баг-репорты поверхностные, без шагов воспроизведения"),
    "08-O3": ("test_data_sql", "same", "strong", None, "SQL знает хорошо, быстро находит данные в базе"),
    "09-S1": ("programming", "up", None, None, "Python: курс пройден на 70%"),
    "09-S2": ("ui_auto", "up", None, None, "Первый автотест на Playwright (с помощью Дмитрия)"),
    "09-O1": ("estimation", "up", None, None, "Оценка трудозатрат лучше: ошибка ~20% вместо 2 раз"),
}
SKILL_VARIANTS = {
    "07-S2": "same — допустимо",
    "09-S1": "same — допустимо",
}
SKILL_NOTES = {
    "05-O1": "у темы «Презентация результатов» нет компетенции в матрице; в базе станет наблюдением по треку",
}
OBSERVATIONS = {
    "04-O2": ("same", "weak", "Бывает резок с коллегами"),
    "06-O2": ("same", "weak", "Не просит помощи, долго молчит, если застряла"),
    "08-O2": ("same", "weak", "Соблюдение сроков"),
    "08-O4": ("same", "strong", "Не боится задавать вопросы"),
}
SIGNALS = {
    "01-R1": ("mood", "positive"), "01-R2": ("goal", "positive"),
    "02-R1": ("burnout", "risk"), "02-R2": ("attrition_risk", "risk"), "02-R3": ("other", "risk"),
    "03-R1": ("burnout", "risk"), "03-R2": ("goal", "positive"),
    "04-R1": ("motivation", "risk"), "04-R2": ("conflict", "risk"),
    "05-R1": ("goal", "positive"), "05-R2": ("conflict", "positive"),
    "06-R1": ("other", "neutral"), "06-R2": ("goal", "positive"),
    "07-R1": ("other", "risk"), "07-R2": ("mood", "positive"),
    "08-R1": ("other", "risk"), "08-R2": ("probation", "risk"),
    "09-R1": ("achievement", "positive"), "09-R2": ("motivation", "positive"),
    "10-R1": ("attrition_risk", "risk"), "10-R2": ("mood", "positive"), "10-R3": ("goal", "positive"),
    "10-R4": ("achievement", "positive"), "10-R5": ("achievement", "positive"),
}
SIGNAL_NOTES = {"06-R1": "факт без оценки — тон neutral (добавлен в схему 14.11.2026)"}
# ID: (тип срока, value, value_to)
TASKS = {
    "01-T1": ("date", "2026-09-30", None), "01-T2": ("date", "2026-09-16", None),
    "01-A1": ("recurring", None, None), "01-T3": ("date", "2026-10-16", None),
    "02-T1": ("date", "2026-09-25", None), "02-T2": ("date", "2026-09-18", None), "02-T3": ("none", None, None),
    "03-T1": ("from", "2026-09-28", None), "03-T2": ("date", "2026-09-18", None),
    "03-T3": ("date", "2026-09-18", None), "03-T4": ("date", "2026-09-30", None),
    "04-T1": ("date", "2026-10-01", None), "04-T2": ("range", "2026-09-21", "2026-09-25"), "04-T3": ("event", None, None),
    "05-T1": ("event", None, None), "05-T2": ("date", "2026-10-02", None),
    "05-A1": ("recurring", "2026-09-21", None), "05-A2": ("none", None, None),
    "06-A1": ("recurring", "2026-09-14", None), "06-T1": ("date", "2026-10-09", None), "06-A2": ("none", None, None),
    "07-T1": ("date", "2026-10-31", None), "07-A1": ("recurring", None, None), "07-T2": ("date", "2026-09-22", None),
    "08-T1": ("date", "2026-09-25", None), "08-A1": ("recurring", None, None), "08-A2": ("none", None, None),
    "08-T2": ("date", "2026-10-07", None),
    "09-T1": ("date", "2026-09-30", None), "09-T2": ("from", "2026-09-28", None), "09-T3": ("date", "2026-10-09", None),
    "10-T1": ("date", "2026-10-07", None), "10-A1": ("recurring", "2026-09-28", None),
    "10-T2": ("date", "2026-10-16", None), "10-T3": ("none", None, None), "10-T4": ("date", "2026-09-30", None),
}
TASK_NOTES = {
    "09-T2": "с календарём в контексте «со следующего спринта» = 28.09 выводится из даты встречи",
    "06-A1": "«с этого спринта»: спринт S2026-19 начался 14.09",
    "06-A2": "условное правило без срока — none",
    "08-A2": "условное правило без срока — none",
}
# Разметка v3: новые вердикты для пунктов, где тема бота не совпала с темой эталона
V3_PARTIAL = {
    "02-S1": ("Додуманные детали", "Бот: «Нагрузочное тестирование (k6, JMeter)» — инструментов в речи нет"),
    "05-S4": ("Додуманные детали", "Бот: «Нагрузочное тестирование (k6, JMeter)» — инструментов в речи нет"),
    "09-S1": ("Додуманные детали", "Бот: «Автоматизация на Python (pytest)» — pytest в записи 9 не звучит"),
    "03-S2": ("Неверная тема навыка", "Бот: «Регрессионное тестирование», эталон: регрессионная модель — соседняя тема"),
    "04-O1": ("Неверная тема навыка", "Бот записал анализ логов как «Документирование дефектов» — тема bug_reports вместо bug_localization"),
}
# Лишние навыки v3, которые остаются ошибкой при схеме 1.0. Остальные прежние FP «вне матрицы» —
# это те же пункты бота, что сопоставлены с бывшими наблюдениями, теперь они учитываются один раз.
SKILL_FP_KEEP = {
    (1, "Автоматизация на Python"): "План («пройти базовый модуль»), а не оценка уровня",
    (1, "Playwright"): "План («будет писать»), а не оценка уровня",
    (8, "Оценка трудозатрат"): "Выведено из договорённости «сначала оценивает», оценки уровня в речи нет",
    (10, "Архитектура фреймворка"): "Желание участвовать в архитектуре (это 10-R3), а не оценка уровня",
}
FP_TYPE = {}
# Пункты бота, которые раньше были сопоставлены с эталоном, а при схеме 1.0 стали лишними
NEW_FP = [
    [10, None, "Навыки (темы)", None, None, None, None, None, None,
     "↑ Нагрузочное тестирование (k6, JMeter): Профиль нагрузки для релиза готов, прогон назначен.", None, None,
     "FP", None, None, "LLM", "Навык из задачи", "Схема 1.0: выполненная задача («профиль готов»), а не оценка уровня; инструментов в речи нет"],
]
# Пункты эталона, которые перестали быть навыками
GOLD_OUT = {
    "10-S1": ("Вне оценки", "Статус задачи", "Профиль нагрузки к релизу готов — выполнение задачи 02-T1",
              "не навык, а выполненная задача (замечание 14.11.2026); пригодится для закрытия задач по открытым задачам сотрудника (D07)"),
}

NEW_COLS = ["Тема (skill_id)", "Направление", "Оценка", "Тип сигнала", "Тон", "Тип срока",
            "Срок: value", "Срок: value_to", "Схема 1.0: заметка"]


def style_like(dst, src):
    dst.font = copy.copy(src.font)
    dst.fill = copy.copy(src.fill)
    dst.border = copy.copy(src.border)
    dst.alignment = copy.copy(src.alignment)
    dst.number_format = src.number_format


def update_gold(ws):
    hdr = {c.value: c.column for c in ws[1]}
    base = ws.max_column
    for i, name in enumerate(NEW_COLS, 1):
        c = ws.cell(1, base + i, name)
        style_like(c, ws.cell(1, base))
        ws.column_dimensions[c.column_letter].width = 14 if i < 9 else 46
    col = {name: base + i for i, name in enumerate(NEW_COLS, 1)}
    seen = set()
    for r in range(2, ws.max_row + 1):
        gid = ws.cell(r, hdr["ID"]).value
        if not gid:
            continue
        for name in NEW_COLS:
            style_like(ws.cell(r, col[name]), ws.cell(r, base))
        note = None
        if gid in GOLD_OUT:
            g, t, text, note = GOLD_OUT[gid]
            ws.cell(r, hdr["Группа"]).value = g
            ws.cell(r, hdr["Тип"]).value = t
            ws.cell(r, hdr["Что"]).value = text
            ws.cell(r, hdr["Обязательность"]).value = "—"
            ws.cell(r, col["Схема 1.0: заметка"]).value = note
            seen.add(gid)
            continue
        if gid in SKILLS:
            topic, d, a, must, text = SKILLS[gid]
            was_obs = ws.cell(r, hdr["Группа"]).value == OBS
            ws.cell(r, hdr["Группа"]).value = SKILL
            ws.cell(r, hdr["Тип"]).value = "Навык"
            ws.cell(r, hdr["Что"]).value = text
            if must:
                ws.cell(r, hdr["Обязательность"]).value = must
            if gid in SKILL_VARIANTS:
                ws.cell(r, hdr["Допустимые варианты"]).value = SKILL_VARIANTS[gid]
            ws.cell(r, col["Тема (skill_id)"]).value = topic
            ws.cell(r, col["Направление"]).value = d
            ws.cell(r, col["Оценка"]).value = a
            note = "бывшее наблюдение «вне матрицы» — теперь тема" if was_obs else "колонка матрицы → тема"
            if gid in SKILL_NOTES:
                note += "; " + SKILL_NOTES[gid]
        elif gid in OBSERVATIONS:
            d, a, text = OBSERVATIONS[gid]
            ws.cell(r, hdr["Что"]).value = text
            ws.cell(r, col["Направление"]).value = d
            ws.cell(r, col["Оценка"]).value = a
            note = "поведение, не компетенция матрицы — остаётся наблюдением"
        elif gid in SIGNALS:
            t, tone = SIGNALS[gid]
            ws.cell(r, col["Тип сигнала"]).value = t
            ws.cell(r, col["Тон"]).value = tone
            note = SIGNAL_NOTES.get(gid)
        elif gid in TASKS:
            t, v, vt = TASKS[gid]
            ws.cell(r, col["Тип срока"]).value = t
            ws.cell(r, col["Срок: value"]).value = v
            ws.cell(r, col["Срок: value_to"]).value = vt
            note = TASK_NOTES.get(gid)
        else:
            continue
        seen.add(gid)
        if note:
            ws.cell(r, col["Схема 1.0: заметка"]).value = note
    missing = (set(SKILLS) | set(OBSERVATIONS) | set(SIGNALS) | set(TASKS)) - seen
    assert not missing, missing


def update_marking(ws):
    # 1) пункты эталона
    for r in range(2, 91):
        gid = ws.cell(r, 2).value
        if gid in GOLD_OUT:
            g, t, text, note = GOLD_OUT[gid]
            ws.cell(r, 3).value, ws.cell(r, 4).value, ws.cell(r, 7).value = g, t, text
            ws.cell(r, 5).value = "—"
            for c in range(10, 18):
                ws.cell(r, c).value = None
            ws.cell(r, 13).value = "Не учитывать"
            ws.cell(r, 18).value = "Схема 1.0: " + note + ". Пункт бота — среди лишних"
            continue
        if gid in SKILLS:
            ws.cell(r, 3).value = SKILL
            ws.cell(r, 4).value = "Навык"
            ws.cell(r, 7).value = SKILLS[gid][4]
            if SKILLS[gid][3]:
                ws.cell(r, 5).value = SKILLS[gid][3]
            old = ws.cell(r, 18).value or ""
            if gid in V3_PARTIAL:
                ws.cell(r, 13).value = "Частично"
                ws.cell(r, 16).value = "LLM"
                ws.cell(r, 17).value = V3_PARTIAL[gid][0]
                ws.cell(r, 18).value = "Схема 1.0: " + V3_PARTIAL[gid][1]
            elif "Есть, но в блоке навыков" in old:
                ws.cell(r, 18).value = "Схема 1.0: бот верно записал в навыки. " + old.replace("Есть, но в блоке навыков", "").strip(" ;().")
        elif gid in OBSERVATIONS:
            ws.cell(r, 7).value = OBSERVATIONS[gid][2]
    # 2) лишние пункты бота: пересобрать блок после строки-заголовка
    start = next(r for r in range(2, ws.max_row + 1) if str(ws.cell(r, 1).value or "").startswith("↓"))
    rows, kept = [], set()
    for r in range(start + 1, ws.max_row + 1):
        vals = [ws.cell(r, c).value for c in range(1, 19)]
        if not any(vals):
            continue
        if vals[2] == "Навыки матрицы":
            text = vals[9] or ""
            key = next((k for k in SKILL_FP_KEEP if k[0] == vals[0] and k[1] in text), None)
            if not key:
                continue                      # тот же пункт бота уже сопоставлен с эталоном
            kept.add(key)
            vals[2] = SKILL
            vals[16] = FP_TYPE.get(key, "Навык из планов")
            vals[17] = "Схема 1.0: " + SKILL_FP_KEEP[key]
        rows.append(vals)
    assert kept == set(SKILL_FP_KEEP), set(SKILL_FP_KEEP) - kept
    rows += [list(v) for v in NEW_FP]
    rows.sort(key=lambda v: int(v[0]))
    tmpl = start + 2
    for r in range(start + 1, ws.max_row + 1):
        for c in range(1, 19):
            ws.cell(r, c).value = None
    for i, vals in enumerate(rows):
        for c, v in enumerate(vals, 1):
            ws.cell(start + 2 + i, c).value = v
    for dv in ws.data_validations.dataValidation:
        if dv.formula1 and "Навыки матрицы" in dv.formula1:
            dv.formula1 = dv.formula1.replace("Навыки матрицы", SKILL)
    return len(rows)


def update_rules(ws):
    texts = {
        10: "3. Срок пересчитан от даты встречи в календарную дату. Относительная формулировка («до пятницы») без даты — «Частично», если правильная дата однозначна. С v4 даты в выжимке — YYYY-MM-DD, тип срока сверяется с колонкой «Тип срока» эталона.",
        14: "7. Навык = TP, если совпадает тема из словаря тем (колонка «Тема» эталона). Соседняя тема — «Частично», тип «Неверная тема навыка». План или желание («пройдёт курс», «будет писать») в блоке навыков — FP, тип «Навык из планов»; выполненная задача («профиль готов») — FP, тип «Навык из задачи». Тема верна, но в пункте есть то, чего нет в речи (инструменты, цифры), — «Частично», тип «Додуманные детали». Направление (same / up / down) и оценка (strong / weak) в F-меру не входят: для v4 они считаются отдельно — доля верных среди TP. У v3 их нет, поэтому v3 и v4 сравниваются только по теме.",
        18: "11. «Наблюдения» — поведение вне компетенций матрицы (резкость, просит ли помощи, сроки) и «Для продукта» в итоговые F1 не входят. Бывшие наблюдения «вне матрицы» (баг-репорты, SQL, трудозатраты, анализ логов, архитектура фреймворка, требования, презентация, управленческий опыт) с 13.11.2026 — навыки по темам.",
        22: "Ни одного FN, FP или «Частично» в группах «Задачи и договорённости», «Навыки (темы)», «Сигналы и риски».",
    }
    for r, t in texts.items():
        ws.cell(r, 1).value = t
    r = ws.max_row + 2
    ws.cell(r, 1).value = "Схема 1.0 (13.11.2026)"
    ws.cell(r, 1).font = copy.copy(ws.cell(7, 1).font)
    ws.cell(r + 1, 1).value = ("Эталон переразмечен по схеме выжимки 1.0: навыки — темы словаря (app.topics), направление same / up / down "
                               "и оценка strong / weak, у сигналов — тип и тон, у задач — тип срока. Разметка v3 пересчитана по новым правилам; "
                               "строка 12.11 в «Журнале» сохранена значениями, строка 13.11 считается формулами.")
    ws.cell(r + 1, 1).alignment = copy.copy(ws.cell(3, 1).alignment)


def update_error_types(ws):
    ws["A9"].value, ws["B9"].value = "Навык вне словаря тем", "В блоке навыков то, чего нет в словаре тем"
    ws["A10"].value, ws["B10"].value = "Неверная тема навыка", "Навык отнесён не к той теме"
    ws["B11"].value = "same / up / down перепутаны"
    extra = [("Навык из планов", "В навыках план или желание («пройдёт курс», «будет писать»), а не оценка уровня"),
             ("Навык из задачи", "В навыках выполненная или назначенная задача («профиль готов»), а не оценка уровня"),
             ("Додуманные детали", "В пункте факты, которых нет в речи: инструменты, цифры, имена")]
    for r, (name, desc) in enumerate(extra, 17):
        ws.cell(r, 1).value, ws.cell(r, 2).value = name, desc
        for c in (3, 4):
            ws.cell(r, c).value = ws.cell(16, c).value.replace("A16", f"A{r}").replace("C16", f"C{r}")
        for c in range(1, 5):
            style_like(ws.cell(r, c), ws.cell(16, c))


def main(src, dst):
    wb = load_workbook(src)
    vals = load_workbook(src, data_only=True)
    update_gold(wb["Эталон"])
    n_extra = update_marking(wb["Разметка"])
    update_rules(wb["Правила"])
    update_error_types(wb["Типы ошибок"])
    m = wb["Метрики"]
    m["A6"].value = SKILL
    m["A1"].value = "Метрики первой оценки (сквозная: аудио → GigaAM → GigaChat) · эталон по схеме 1.0"
    p = wb["Проверки"]
    p["F1"].value = "Навыки из словаря тем"
    p["G1"].value = "Даты в ISO"
    # Журнал: строка 12.11 — значениями, новая строка — формулами
    j, jv = wb["Журнал"], vals["Журнал"]
    for c in range(1, j.max_column + 1):
        src_cell = j.cell(2, c)
        if isinstance(src_cell.value, str) and src_cell.value.startswith("="):
            j.cell(3, c).value = src_cell.value
            src_cell.value = jv.cell(2, c).value
        else:
            j.cell(3, c).value = src_cell.value
        style_like(j.cell(3, c), src_cell)
    j["A3"].value = "13.11.2026"
    j["B3"].value = "v0 (пересчёт: эталон по схеме 1.0)"
    j["T3"].value = "см. «Типы ошибок»"
    j["U3"].value = "Базовая линия для сравнения с v4. Навыки сравниваются по теме; направление и оценка — только у v4"
    j["V3"].value = "Пересчёт той же выжимки v3 от 12.11.2026 по новому эталону: ответы модели не менялись"
    j["A4"].value = ("Строка 12.11 сохранена значениями (старые правила: 8 колонок матрицы). Строка 13.11 ссылается на текущую разметку. "
                     "Перед следующим прогоном скопируйте её и вставьте как значения.")
    wb.save(dst)
    print("лишних пунктов бота в разметке:", n_extra)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
