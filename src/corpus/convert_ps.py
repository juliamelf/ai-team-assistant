"""Конвертер профстандартов (docx из КонсультантПлюс) в Markdown по контракту data/README.md.

Запуск:
    python src/corpus/convert_ps.py data/raw data/clean data/manifest.csv

Для каждого data/raw/*.docx пишет data/clean/ps-<код>.md и строку в manifest.csv.
Источники шаблона матрицы компетенций — SOURCES, остальные профстандарты получают doc_role: distractor.
"""
import csv
import datetime as dt
import hashlib
import re
import sys
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

SOURCES = {"06.004", "06.016"}

TF_LABELS = ["Трудовые действия", "Необходимые умения", "Необходимые знания", "Другие характеристики"]
OTF_LABELS = [
    "Возможные наименования должностей, профессий",
    "Требования к образованию и обучению",
    "Требования к опыту практической работы",
    "Особые условия допуска к работе",
    "Другие характеристики",
]
OTF_CODE = re.compile(r"^[A-Z]$")
TF_CODE = re.compile(r"^[A-Z]/\d{2}\.\d$")
PS_CODE = re.compile(r"^\d{2}\.\d{3}$")
FOOTNOTE = re.compile(r"\s*<\d+>")


def norm(text: str) -> str:
    """Схлопывает пробелы и убирает сноски вида <3>."""
    return " ".join(FOOTNOTE.sub("", text).split())


def cell_items(cell) -> list[str]:
    """Пункты ячейки: каждый абзац — отдельный пункт; «-» и пустые отбрасываются."""
    items = [norm(p.text) for p in cell.paragraphs]
    return [i for i in items if i and i != "-"]


def row_cells(row) -> list:
    """Ячейки строки без повторов объединённых ячеек."""
    out, prev = [], None
    for c in row.cells:
        if c._tc is prev:
            continue
        prev = c._tc
        out.append(c)
    return out


def body_items(doc):
    for el in doc.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield "p", Paragraph(el, doc)
        elif tag == "tbl":
            yield "t", Table(el, doc)


def parse(path: Path) -> dict:
    doc = Document(str(path))
    meta = {"title": None, "ps_code": None, "activity": None, "goal": None,
            "order": None, "valid_to": None}
    otfs = []          # [{code, name, level, info: {label: [items]}, tfs: [...]}]
    state = "pre"      # pre → otf/tf → end
    current = None     # текущая ОТФ или ТФ
    expect_goal = False
    for kind, item in body_items(doc):
        if kind == "p":
            text = norm(item.text)
            if not text:
                continue
            if text.startswith("IV. Сведения об организациях"):
                state = "end"
            if meta["order"] is None:
                m = re.match(r"от (\d{1,2} \w+ \d{4}) г\. N (\d+н)", text)
                if m:
                    meta["order"] = f"приказ Минтруда России от {m.group(1)} г. № {m.group(2)}"
            m = re.search(r"действует до (\d{1,2} \w+ \d{4}) г", text)
            if m:
                meta["valid_to"] = m.group(1)
            expect_goal = text.startswith(("Основная цель вида профессиональной деятельности",
                                            "Краткое описание вида профессиональной деятельности"))
            continue

        if state == "end":
            continue
        rows = [row_cells(r) for r in item.rows]
        texts = [[norm(c.text) for c in r] for r in rows]
        first = texts[0][0] if texts and texts[0] else ""

        if meta["title"] is None:
            joined = " ".join(" ".join(r) for r in texts)
            m = re.search(r'профессионального стандарта "(.+?)" \(', joined)
            if m:
                meta["title"] = m.group(1)
        if meta["ps_code"] is None and texts and len(texts[0]) >= 2 and PS_CODE.match(texts[0][-1]):
            meta["ps_code"] = texts[0][-1]
            meta["activity"] = texts[0][0]
            continue
        if expect_goal:
            meta["goal"] = first
            expect_goal = False
            continue

        if first == "Наименование" and len(texts[0]) >= 6:
            name, code, level = texts[0][1], texts[0][3], texts[0][5]
            if OTF_CODE.match(code):
                current = {"code": code, "name": name, "level": level, "info": {}, "tfs": []}
                otfs.append(current)
                state = "otf"
            elif TF_CODE.match(code):
                current = {"code": code, "name": name, "level": level, "blocks": {}}
                otfs[-1]["tfs"].append(current)
                state = "tf"
            continue

        labels = TF_LABELS if state == "tf" else OTF_LABELS if state == "otf" else []
        target = current["blocks"] if state == "tf" else current["info"] if state == "otf" else None
        for r in rows:
            if len(r) < 2:
                continue
            label = norm(r[0].text)
            if label in labels:
                items = []
                for c in r[1:]:
                    items.extend(cell_items(c))
                if items:
                    target.setdefault(label, []).extend(items)
    return {"meta": meta, "otfs": otfs}


def to_markdown(data: dict, source_url: str = "") -> str:
    meta, otfs = data["meta"], data["otfs"]
    ps = meta["ps_code"]
    tf_codes = [f"{ps}:{tf['code']}" for o in otfs for tf in o["tfs"]]
    role = "source" if ps in SOURCES else "distractor"
    lines = [
        "---",
        f"doc_id: ps-{ps}",
        f'ps_code: "{ps}"',
        f"title: {meta['title']}",
        "doc_type: profstandard",
        f"doc_role: {role}",
        f"version: {meta['order']}",
        f"valid_to: {meta['valid_to'] or ''}",
        f"source_url: {source_url}",
        "tf_codes: [" + ", ".join(f'"{c}"' for c in tf_codes) + "]",
        "---",
        "",
        f"# Профстандарт {ps} «{meta['title']}»",
        "",
        f"Вид профессиональной деятельности: {meta['activity']}.",
        "",
        f"Основная цель: {meta['goal']}",
        "",
    ]
    for o in otfs:
        lines += [f"## {ps}:{o['code']} — {o['name']} (уровень квалификации {o['level']})", ""]
        for label in OTF_LABELS:
            items = o["info"].get(label)
            if items:
                lines += [f"{label}: " + "; ".join(items) + ".", ""]
        for tf in o["tfs"]:
            lines += [
                f"### {ps}:{tf['code']} — {tf['name']}",
                "",
                f"Обобщённая трудовая функция: {ps}:{o['code']} — {o['name']}. "
                f"Уровень квалификации {tf['level']}.",
                "",
            ]
            for label in TF_LABELS:
                items = tf["blocks"].get(label)
                if items:
                    lines += [f"#### {label}"] + [f"- {i}" for i in items] + [""]
    return "\n".join(lines).rstrip() + "\n"


def main(raw_dir: str, clean_dir: str, manifest_path: str) -> None:
    raw, clean = Path(raw_dir), Path(clean_dir)
    clean.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(raw.glob("*.docx")):
        data = parse(path)
        meta = data["meta"]
        if not meta["ps_code"]:
            print(f"ПРОПУЩЕН: {path.name} — не найден код профстандарта", file=sys.stderr)
            continue
        md = to_markdown(data)
        out = clean / f"ps-{meta['ps_code']}.md"
        out.write_text(md, encoding="utf-8", newline="\n")
        tf_count = sum(len(o["tfs"]) for o in data["otfs"])
        rows.append({
            "doc_id": f"ps-{meta['ps_code']}",
            "file": f"data/clean/{out.name}",
            "ps_code": meta["ps_code"],
            "doc_role": "source" if meta["ps_code"] in SOURCES else "distractor",
            "version": meta["order"],
            "tf_count": tf_count,
            "chars": len(md),
            "sha256_raw": hashlib.sha256(path.read_bytes()).hexdigest(),
            "added": dt.date.today().isoformat(),
        })
        print(f"{out.name}: ОТФ {len(data['otfs'])}, ТФ {tf_count}, {len(md)} символов")
    with open(manifest_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
