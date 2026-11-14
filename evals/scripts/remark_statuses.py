"""Правки эталона v0 от 14.11.2026: статусы задач (блок task_updates схемы 1.0).

Запуск:  python3 remark_statuses.py e46_разметка_v0_v3_схема_1.0.xlsx результат.xlsx
Применяется поверх результата remark_v0.py. Исходный файл не меняется.

Что меняется:
  10-S1 удалён — дубль 10-R5.
  Новая группа «Статусы задач»: обновления задач, поставленных на прошлых встречах.
    10-U1  02-T1 «Профиль нагрузки»               done         (было 10-R5)
    10-U2  02-T2 «Снять с Павла дежурства»          done         (новый)
    10-U3  02-T3 «Инструкция по нагрузочному стенду» rescheduled (было 10-T2, новый срок 16.10)
    10-U4  05-T1 «Обсудить с Павлом роль Дмитрия»   done         (новый)
    10-U5  05-A2 «Стать дублёром Павла по нагрузке»  in_progress  (было 10-R4)
  10-R3 → 10-A2, договорённость, обязательная.
  05-A2 — обязательная.
"""
import copy
import sys

from openpyxl import load_workbook

GROUP = "Статусы задач"
TASKS = "Задачи и договорённости"
UPD_TYPE = "Обновление задачи"
NEW_ERR = [
    ("Пропущенное обновление задачи", "Статус или новый срок задачи с прошлой встречи не попал в task_updates"),
    ("Новая задача вместо обновления", "Обновление прошлой задачи записано новой задачей в tasks"),
]

# Эталон: ID -> изменения. «new» — строка добавляется (копия стиля строки 10-R5).
GOLD = {
    "10-S1": "delete",
    "10-R5": dict(id="10-U1", group=GROUP, type=UPD_TYPE, must="обязательный", who="Павел",
                  what="Профиль нагрузки к релизу 5.0 готов", status="done", ref="02-T1",
                  changed="Было 10-R5 (сигнал) и дубль 10-S1 (навык); статус задачи 02-T1 (замечание 14.11.2026)"),
    "NEW-U2": dict(id="10-U2", group=GROUP, type=UPD_TYPE, must="обязательный", who="Татьяна",
                   what="Дежурство с Павла сняли", status="done", ref="02-T2",
                   changed="Новый пункт: статус задачи 02-T2 (14.11.2026)"),
    "10-T2": dict(id="10-U3", group=GROUP, type=UPD_TYPE, who="Павел",
                  what="Инструкция по нагрузочному стенду: назначен срок", status="rescheduled", ref="02-T3",
                  variants="новая задача с тем же сроком — «Частично»",
                  changed="Было 10-T2 (новая задача); по схеме 1.0 — новый срок задачи 02-T3 (14.11.2026)"),
    "NEW-U4": dict(id="10-U4", group=GROUP, type=UPD_TYPE, must="обязательный", who="Татьяна",
                   what="Роль Дмитрия как дублёра с Павлом обсудили", status="done", ref="05-T1",
                   changed="Новый пункт: статус задачи 05-T1 (14.11.2026)"),
    "10-R4": dict(id="10-U5", group=GROUP, type=UPD_TYPE, must="обязательный", who="Дмитрий",
                  what="Дублёр по нагрузке: обучение начато, Павел согласен обучать Дмитрия", status="in_progress", ref="05-A2",
                  changed="Было 10-R4 (сигнал, допустимый); статус договорённости 05-A2 (замечание 14.11.2026)"),
    "10-R3": dict(id="10-A2", group=TASKS, type="Договорённость", must="обязательный", who="Павел",
                  what="Павел больше участвует в архитектурных решениях", due_text="срок не назван", due_type="none",
                  changed="Было 10-R3 (сигнал, допустимый): желание сотрудника — договорённость (замечание 14.11.2026)"),
    "05-A2": dict(must="обязательный", changed="Допустимый → обязательный (замечание 14.11.2026)"),
}
# Порядок пунктов записи 10 после правки
ORDER10 = ["10-T1", "10-A1", "10-T3", "10-T4", "10-A2", "10-U1", "10-U2", "10-U3", "10-U4", "10-U5", "10-R1", "10-R2"]

# Разметка v3 по изменённым пунктам: (вердикт, источник, тип ошибки, заметка); None — не трогать бот-колонки
MARK = {
    "10-U1": ("FN", "LLM", "Пропущенное обновление задачи", "Бот записал как навык «Нагрузка» (лишний пункт ниже); блока обновлений у v3 нет"),
    "10-U2": ("FN", "LLM", "Пропущенное обновление задачи", "«Дежурство с него сняли» есть только в summary; блока обновлений у v3 нет"),
    "10-U3": ("Частично", "LLM", "Новая задача вместо обновления", "Бот поставил новую задачу с верным сроком 16.10; по схеме 1.0 это новый срок задачи 02-T3"),
    "10-U4": ("FN", "LLM", "Пропущенное обновление задачи", "Не найден; блока обновлений у v3 нет"),
    "10-U5": ("FN", "LLM", "Пропущенное обновление задачи", "Не найден; блока обновлений у v3 нет"),
    "10-A2": ("FN", "LLM", "Пропущенная задача", "Бот записал желание в навык «Архитектура» (лишний пункт ниже)"),
    "05-A2": ("FN", "LLM", "Пропущенная задача", "Есть только в наблюдении к навыку «Нагрузка»"),
}
RULE = ("Статусы задач (14.11.2026). Если на встрече сказано, что задача с прошлой встречи начата, выполнена, отменена "
        "или у неё новый срок, это пункт группы «Статусы задач» (в выжимке — блок task_updates со ссылкой на задачу). "
        "TP — найдено обновление нужной задачи с верным статусом (для переноса — и сроком). Та же суть, записанная новой задачей, — "
        "«Частично», тип «Новая задача вместо обновления». Не найдено — FN, тип «Пропущенное обновление задачи». "
        "Желание сотрудника, под которое руководитель что-то обещает («хочет участвовать в архитектуре» → «добавлю в комитет»), — договорённость, а не сигнал. "
        "Группа входит в micro и macro F1 (β = 2: пропуск хуже лишнего). У v3 блока обновлений нет, поэтому его пункты — FN.")


def style_like(dst, src):
    dst.font = copy.copy(src.font)
    dst.fill = copy.copy(src.fill)
    dst.border = copy.copy(src.border)
    dst.alignment = copy.copy(src.alignment)
    dst.number_format = src.number_format


def read_rows(ws, first, last, ncol):
    return [{"vals": [ws.cell(r, c).value for c in range(1, ncol + 1)], "src": r} for r in range(first, last + 1)]


def write_rows(ws, rows, first, ncol, style_src):
    for i, row in enumerate(rows):
        r = first + i
        for c in range(1, ncol + 1):
            cell = ws.cell(r, c)
            cell.value = row["vals"][c - 1]
            style_like(cell, style_src.cell(row["src"], c))


def reorder_record10(rows, id_col):
    idx = [i for i, row in enumerate(rows) if str(row["vals"][id_col] or "").startswith("10-")]
    by_id = {rows[i]["vals"][id_col]: rows[i] for i in idx}
    assert set(by_id) == set(ORDER10), set(by_id) ^ set(ORDER10)
    out = rows[:idx[0]] + [by_id[g] for g in ORDER10] + rows[idx[-1] + 1:]
    return out


def update_gold(ws, src_ws):
    hdr = {c.value: c.column - 1 for c in ws[1]}
    ncol = ws.max_column
    for name in ("Статус задачи", "Ссылка на задачу"):
        ncol += 1
        c = ws.cell(1, ncol, name)
        style_like(c, ws.cell(1, ncol - 1))
        ws.column_dimensions[c.column_letter].width = 14
        hdr[name] = ncol - 1
    last = max(r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value)
    rows = read_rows(ws, 2, last, ncol)
    tmpl = next(row for row in rows if row["vals"][0] == "10-R5")
    out = []
    for row in rows:
        gid = row["vals"][0]
        ch = GOLD.get(gid)
        if ch == "delete":
            continue
        if ch:
            apply_gold(row["vals"], ch, hdr)
        out.append(row)
    for key in ("NEW-U2", "NEW-U4"):
        vals = list(tmpl["vals"])
        for k in ("Срок", "Допустимые варианты", "Тип сигнала", "Тон", "Схема 1.0: заметка"):
            vals[hdr[k]] = None
        apply_gold(vals, GOLD[key], hdr)
        out.append({"vals": vals, "src": tmpl["src"]})
    out = reorder_record10(out, 0)
    for r in range(2, ws.max_row + 2):
        for c in range(1, ncol + 1):
            ws.cell(r, c).value = None
    write_rows(ws, out, 2, ncol, src_ws)
    ws.auto_filter.ref = f"A1:{ws.cell(1, ncol).column_letter}{len(out) + 1}"
    return out, hdr


def apply_gold(v, ch, hdr):
    if "id" in ch:
        v[hdr["ID"]] = ch["id"]
    if "group" in ch:
        v[hdr["Группа"]] = ch["group"]
        v[hdr["Тип"]] = ch["type"]
        v[hdr["Тип сигнала"]] = v[hdr["Тон"]] = None
    for key, col in (("must", "Обязательность"), ("who", "Кто"), ("what", "Что"), ("variants", "Допустимые варианты"),
                     ("changed", "Что изменено"), ("status", "Статус задачи"), ("ref", "Ссылка на задачу"),
                     ("due_text", "Срок"), ("due_type", "Тип срока")):
        if key in ch:
            v[hdr[col]] = ch[key]


def update_marking(ws, src_ws, gold_rows, ghdr):
    gold = {row["vals"][0]: row["vals"] for row in gold_rows}
    start = next(r for r in range(2, ws.max_row + 1) if str(ws.cell(r, 1).value or "").startswith("↓"))
    rows = read_rows(ws, 2, start - 1, 18)
    tmpl = next(row for row in rows if row["vals"][1] == "10-R5")
    old2new = {k: v["id"] for k, v in GOLD.items() if isinstance(v, dict) and "id" in v and not k.startswith("NEW")}
    out = []
    for row in rows:
        v = row["vals"]
        gid = old2new.get(v[1], v[1])
        if GOLD.get(v[1]) == "delete":
            continue
        if gid in MARK:
            g = gold[gid]
            v[1] = gid
            v[2], v[3], v[4] = g[ghdr["Группа"]], g[ghdr["Тип"]], g[ghdr["Обязательность"]]
            v[5], v[6], v[7], v[8] = g[ghdr["Кто"]], g[ghdr["Что"]], g[ghdr["Срок"]], g[ghdr["Допустимые варианты"]]
            verdict, source, err, note = MARK[gid]
            if verdict == "FN":
                for c in (9, 10, 11, 13, 14):
                    v[c] = None
            v[12], v[15], v[16], v[17] = verdict, source, err, "14.11.2026: " + note
        out.append(row)
    for gid in ("10-U2", "10-U4"):
        g = gold[gid]
        v = [10, gid, g[ghdr["Группа"]], g[ghdr["Тип"]], g[ghdr["Обязательность"]], g[ghdr["Кто"]], g[ghdr["Что"]],
             None, None, None, None, None, *MARK[gid][:1], None, None, *MARK[gid][1:3], "14.11.2026: " + MARK[gid][3]]
        out.append({"vals": v, "src": tmpl["src"]})
    out = reorder_record10(out, 1)
    extra = len(out) - len(rows)
    assert extra == 1, extra
    ws.insert_rows(start, extra)          # блок лишних пунктов сдвигается вниз
    write_rows(ws, out, 2, 18, src_ws)
    ws.auto_filter.ref = f"A1:R{len(out) + 1}"
    for dv in ws.data_validations.dataValidation:
        ref = str(dv.sqref)
        if ref.startswith(("C92", "A92")):
            dv.sqref = dv.sqref.__class__(ref.replace("92", str(92 + extra)))
        if dv.formula1 and "Сигналы и риски" in dv.formula1 and GROUP not in dv.formula1:
            dv.formula1 = dv.formula1.replace("Сигналы и риски", f"Сигналы и риски,{GROUP}")
        if dv.formula1 and "'Типы ошибок'!$A$2:$A$20" in dv.formula1:
            dv.formula1 = "'Типы ошибок'!$A$2:$A$30"
    return start + extra


def group_row(m, r, name, beta, style_r):
    rng = "Разметка!$C$2:$C$171,$A{r},Разметка!$M$2:$M$171,\"{v}\""
    vals = {
        "A": name,
        "B": "=COUNTIFS(" + rng.format(r=r, v="TP") + ")",
        "C": "=COUNTIFS(" + rng.format(r=r, v="Частично") + ")",
        "D": "=COUNTIFS(" + rng.format(r=r, v="FN") + ")",
        "E": "=COUNTIFS(" + rng.format(r=r, v="FP") + ")",
        "F": beta,
        "G": f'=IFERROR(B{r}/(B{r}+E{r}+C{r}),"")',
        "H": f'=IFERROR(B{r}/(B{r}+D{r}+C{r}),"")',
        # F = 0, если пункты в группе есть, а верных нет (иначе AVERAGE пропустит группу)
        "I": f'=IF(B{r}+C{r}+D{r}+E{r}=0,"",IF(N(G{r})+N(H{r})=0,0,2*G{r}*H{r}/(G{r}+H{r})))',
        "J": f'=IF(B{r}+C{r}+D{r}+E{r}=0,"",IF(N(G{r})+N(H{r})=0,0,(1+F{r}^2)*G{r}*H{r}/(F{r}^2*G{r}+H{r})))',
        "K": f'=IFERROR((B{r}+C{r})/(B{r}+C{r}+E{r}),"")',
        "L": f'=IFERROR((B{r}+C{r})/(B{r}+C{r}+D{r}),"")',
        "M": f'=IF(B{r}+C{r}+D{r}+E{r}=0,"",IF(N(K{r})+N(L{r})=0,0,2*K{r}*L{r}/(K{r}+L{r})))',
        "N": f'=COUNTIFS(Эталон!$E:$E,$A{r},Эталон!$G:$G,"обязательный")',
    }
    for col, v in vals.items():
        m[f"{col}{r}"].value = v
        style_like(m[f"{col}{r}"], m[f"{col}{style_r}"])


def total_row(m, r, style_r):
    for col in "BCDEN":
        m[f"{col}{r}"].value = f"=SUM({col}5:{col}8)"
    m[f"G{r}"].value = f'=IFERROR(B{r}/(B{r}+E{r}+C{r}),"")'
    m[f"H{r}"].value = f'=IFERROR(B{r}/(B{r}+D{r}+C{r}),"")'
    m[f"I{r}"].value = f'=IFERROR(2*G{r}*H{r}/(G{r}+H{r}),"")'
    m[f"K{r}"].value = f'=IFERROR((B{r}+C{r})/(B{r}+C{r}+E{r}),"")'
    m[f"L{r}"].value = f'=IFERROR((B{r}+C{r})/(B{r}+C{r}+D{r}),"")'
    m[f"M{r}"].value = f'=IFERROR(2*K{r}*L{r}/(K{r}+L{r}),"")'
    m[f"A{r}"].value = "Итого micro (без наблюдений)"
    for c in "ABCDEFGHIJKLMN":
        style_like(m[f"{c}{r}"], m[f"{c}{style_r}"])


def update_metrics(m):
    # было: 5 задачи, 6 навыки, 7 сигналы, 8 наблюдения, 9 micro, 10 macro
    # стало: 8 статусы задач, 9 наблюдения, 10 micro, 11 macro (строки 5–7 на месте — на них ссылается «Журнал»)
    macro_style = {c: copy.copy(m[f"{c}10"]._style) for c in "ABCDEFGHIJKLMN"}
    for c in "ABCDEFGHIJKLMN":
        m[f"{c}10"].value = m[f"{c}11"].value = None
    total_row(m, 10, 9)
    group_row(m, 9, "Наблюдения", 1, 8)
    for r, (name, beta) in zip((5, 6, 7), (("Задачи и договорённости", 2), ("Навыки (темы)", 0.5), ("Сигналы и риски", 2))):
        group_row(m, r, name, beta, r)
    group_row(m, 8, GROUP, 2, 7)
    for c in "ABCDEFGHIJKLMN":
        m[f"{c}11"]._style = macro_style[c]
    m["A11"].value = "Итого macro (среднее F1 четырёх групп)"
    m["I11"].value = '=IFERROR(AVERAGE(I5:I8),"")'
    m["M11"].value = '=IFERROR(AVERAGE(M5:M8),"")'
    m["A2"].value = ("Строго: «Частично» = ошибка (FN+FP). Мягко: «Частично» = TP. β: F2 — пропуск хуже лишнего "
                     "(задачи, статусы задач, риски), F0.5 — лишнее хуже пропуска (навыки).")


def update_journal(j):
    j["O3"].value = "=Метрики!I10"
    j["P3"].value = "=Метрики!I11"
    for col, name, f in (("W", "Статусы задач: R", "=Метрики!H8"), ("X", "Статусы задач: F2", "=Метрики!J8")):
        j[f"{col}1"].value = name
        style_like(j[f"{col}1"], j["V1"])
        j[f"{col}2"].value = "—"
        j[f"{col}3"].value = f
        for r in (2, 3):
            style_like(j[f"{col}{r}"], j[f"N{r}"])
        j.column_dimensions[col].width = j.column_dimensions["N"].width or 12
    j["A3"].value = "14.11.2026"
    j["B3"].value = "v0 (пересчёт: схема 1.0, статусы задач)"
    j["V3"].value = ("Пересчёт той же выжимки v3 от 12.11.2026 по новому эталону: ответы модели не менялись. "
                     "С 14.11 в micro и macro входит группа «Статусы задач»")


def update_error_types(ws):
    for i, (name, desc) in enumerate(NEW_ERR):
        r = 20 + i
        ws.cell(r, 1).value, ws.cell(r, 2).value = name, desc
        for c in (3, 4):
            ws.cell(r, c).value = ws.cell(19, c).value.replace("A19", f"A{r}").replace("C19", f"C{r}")
        for c in range(1, 5):
            style_like(ws.cell(r, c), ws.cell(19, c))


def update_rules(ws):
    r = ws.max_row + 2
    ws.cell(r, 1).value = "Статусы задач (14.11.2026)"
    style_like(ws.cell(r, 1), ws.cell(r - 2, 1))
    ws.cell(r + 1, 1).value = RULE
    style_like(ws.cell(r + 1, 1), ws.cell(r - 1, 1))
    for row in range(1, ws.max_row + 1):
        v = ws.cell(row, 1).value
        if isinstance(v, str) and v.startswith("Ни одного FN, FP или «Частично» в группах"):
            ws.cell(row, 1).value = ("Ни одного FN, FP или «Частично» в группах «Задачи и договорённости», «Статусы задач», "
                                     "«Навыки (темы)», «Сигналы и риски».")


def main(src, dst):
    wb = load_workbook(src)
    styles = load_workbook(src)
    gold_rows, ghdr = update_gold(wb["Эталон"], styles["Эталон"])
    update_marking(wb["Разметка"], styles["Разметка"], gold_rows, ghdr)
    update_metrics(wb["Метрики"])
    update_journal(wb["Журнал"])
    update_error_types(wb["Типы ошибок"])
    update_rules(wb["Правила"])
    wb.save(dst)
    print("пунктов эталона:", len(gold_rows))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
