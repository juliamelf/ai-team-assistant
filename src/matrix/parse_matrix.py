"""Разбор qa-competency-matrix.xlsx в CSV для загрузки в PostgreSQL.

Запуск:  python3 parse_matrix.py qa-competency-matrix.xlsx out_dir

Результат (UTF-8, разделитель — запятая):
  matrix_version.csv      — файл-источник, SHA-256, дата разбора
  scale.csv               — шкала уровней 0–5
  competencies.csv        — 29 компетенций (трудовые функции)
  grades.csv              — 16 грейдов по трекам
  grade_requirements.csv  — требуемый уровень: грейд × компетенция
  level_descriptions.csv  — знания и навыки: трек × блок × уровень
"""
import csv
import hashlib
import re
import sys
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

TRACK_SHEETS = {"Ручное": "manual", "Автоматизированное": "auto", "Нагрузочное": "load"}
TRACK_BY_NAME = {
    "Ручное тестирование": "manual",
    "Автоматизация": "auto",
    "Нагрузочное тестирование": "load",
    "Управление": "management",
}
NO_ITEMS = "—"
REF_RE = re.compile(r"\(([^()]*)\)\s*$")
PS_RE = re.compile(r"(?:06\.016\s+)?[A-E]/\d{2}\.\d")


def clean(v):
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


def write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return len(rows)


def parse_scale(ws):
    rows = []
    for r in ws.iter_rows(values_only=True):
        if isinstance(r[0], (int, float)):
            rows.append([int(r[0]), clean(r[1]), clean(r[2]), clean(r[3])])
    return rows


def parse_grades(ws):
    rows = list(ws.iter_rows(values_only=True))
    head_i = next(i for i, r in enumerate(rows) if clean(r[0]) == "Код")
    head, tracks, sheets = rows[head_i], rows[head_i - 2], rows[head_i - 1]
    grade_cols = [c for c in range(3, len(head)) if clean(head[c]) and clean(tracks[c])]

    grades, counters = [], {}
    for c in grade_cols:
        track = TRACK_BY_NAME[clean(tracks[c])]
        counters[track] = counters.get(track, 0) + 1
        gid = f"{track}_{counters[track]}"
        grades.append([gid, clean(head[c]), track, counters[track], clean(sheets[c])])

    competencies, requirements = [], []
    block, block_name = "", ""
    for r in rows[head_i + 1:]:
        code, ps, name = clean(r[0]), clean(r[1]), clean(r[2])
        if re.fullmatch(r"[A-E]", code):
            block, block_name = code, ps or name
            continue
        if not re.fullmatch(r"[A-E]\.\d+", code):
            continue
        standard = "06.016" if ps.startswith("06.016") else "06.004"
        competencies.append([code, ps, name, block, block_name, standard, len(competencies) + 1])
        for (gid, *_), c in zip(grades, grade_cols):
            level = r[c]
            if not isinstance(level, (int, float)):
                raise ValueError(f"Нет уровня: {code} / {gid}")
            requirements.append([gid, code, int(level)])
    return grades, competencies, requirements


def parse_track(ws, track, ps_to_code):
    """Знания и навыки. Утверждения относятся к блоку и уровню; код функции — в скобках в конце."""
    rows = [[ws.cell(r, c).value for c in range(1, 13)] for r in range(1, ws.max_row + 1)]
    out, block = [], ""
    for r in rows:
        if re.fullmatch(r"[A-E]", clean(r[0])):
            block = clean(r[0])
            continue
        if not block:
            continue
        for c in range(2, 12):
            text = clean(r[c])
            if not text or text == NO_ITEMS:
                continue
            level = (c - 2) // 2 + 1
            kind = "knowledge" if (c - 2) % 2 == 0 else "skill"
            ref, is_extra, is_note = "", False, False
            m = REF_RE.search(text)
            if m:
                inner = m.group(1)
                found = PS_RE.findall(inner)
                if found:
                    ref = "; ".join(found)
                    text = text[: m.start()].strip()
                elif inner.strip().startswith("доп"):
                    is_extra = True
                    text = text[: m.start()].strip()
            if text.startswith("Требования уровня"):
                is_note = True
            codes = "; ".join(ps_to_code[p] for p in ref.split("; ") if p in ps_to_code)
            out.append([track, block, level, kind, text, ref, codes, int(is_extra), int(is_note)])
    return out


def main(src, out_dir):
    src, out = Path(src), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    wb = load_workbook(src, data_only=True)

    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    n = {}
    n["matrix_version"] = write(out / "matrix_version.csv", ["source_file", "sha256", "parsed_on"],
                                [[src.name, sha, date.today().isoformat()]])
    n["scale"] = write(out / "scale.csv", ["level", "name", "meaning", "typical_grade"], parse_scale(wb["Шкала"]))

    grades, competencies, requirements = parse_grades(wb["Грейды"])
    n["grades"] = write(out / "grades.csv", ["grade_id", "name", "track", "grade_order", "matrix_sheet"], grades)
    n["competencies"] = write(out / "competencies.csv",
                              ["code", "ps_code", "name", "block", "block_name", "standard", "sort_order"], competencies)
    n["grade_requirements"] = write(out / "grade_requirements.csv", ["grade_id", "competency_code", "required_level"],
                                    requirements)

    # код профстандарта → код компетенции; неоднозначные подписи (E.8 и E.9) не сопоставляются
    seen = {}
    for code, ps, *_ in competencies:
        seen.setdefault(ps, []).append(code)
    ps_to_code = {ps: codes[0] for ps, codes in seen.items() if len(codes) == 1}

    desc = []
    for sheet, track in TRACK_SHEETS.items():
        desc += parse_track(wb[sheet], track, ps_to_code)
    n["level_descriptions"] = write(
        out / "level_descriptions.csv",
        ["track", "block", "level", "kind", "text", "ps_ref", "competency_codes", "is_extra", "is_note"], desc)

    for k, v in n.items():
        print(f"{k}: {v}")
    no_ref = sum(1 for d in desc if not d[5] and not d[7] and not d[8])
    unmapped = sum(1 for d in desc if d[5] and not d[6])
    print(f"утверждений без кода функции: {no_ref}; с кодом, которого нет среди компетенций: {unmapped}")
    print("sha256:", sha)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
