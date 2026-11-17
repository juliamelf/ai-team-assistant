"""Проверки корпуса перед индексацией (раздел «Проверки» в data/README.md).

Запуск:
    python src/corpus/check_corpus.py data/clean data/manifest.csv db/seed/matrix/competencies.csv [data/raw]

Код выхода 0 — ошибок нет; 1 — есть ошибки. Предупреждения код выхода не меняют.
"""
import csv
import re
import sys
from pathlib import Path

REQUIRED = ["doc_id", "ps_code", "title", "doc_type", "doc_role", "version", "tf_codes"]
NOISE = ["КонсультантПлюс", "consultant.ru", "Гарант", "Документ предоставлен", "Страница", "стр.", "ОКЗ", "None"]
TF_HEADER = re.compile(r"^### (\d{2}\.\d{3}:[A-Z]/\d{2}\.\d) — .+")
TF_CODE = re.compile(r"\b[A-Z]/\d{2}\.\d\b")


def front_matter(text: str) -> dict:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    meta = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def competency_key(row: dict) -> str | None:
    """Ключ компетенции 06.004:A/01.4 из competencies.csv; None, если кода ТФ нет."""
    code = TF_CODE.search(row["ps_code"])
    return f"{row['standard']}:{code.group(0)}" if code else None


def raw_tf_count(raw_dir: Path, ps_code: str) -> int | None:
    from docx import Document
    for path in raw_dir.glob(f"{ps_code.replace('.', '-')}*.docx"):
        doc = Document(str(path))
        codes = set()
        for t in doc.tables:
            for r in t.rows:
                for c in r.cells:
                    codes.update(TF_CODE.findall(c.text))
        return len(codes)
    return None


def main(clean_dir: str, manifest_path: str, competencies_path: str, raw_dir: str | None = None) -> int:
    errors, warnings = [], []
    manifest = {r["doc_id"]: r for r in csv.DictReader(open(manifest_path, encoding="utf-8"))}
    headers = {}  # ключ ТФ → doc_id
    for path in sorted(Path(clean_dir).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta = front_matter(text)
        if not meta:
            errors.append(f"{path.name}: нет блока метаданных")
            continue
        for key in REQUIRED:
            if not meta.get(key) or meta[key] == "None":
                errors.append(f"{path.name}: пустое поле {key}")
        doc_id = meta.get("doc_id")
        if path.stem != doc_id:
            errors.append(f"{path.name}: doc_id {doc_id} не совпадает с именем файла")
        found = [m.group(1) for line in text.splitlines() if (m := TF_HEADER.match(line))]
        listed = re.findall(r'"([^"]+)"', meta.get("tf_codes", ""))
        if found != listed:
            errors.append(f"{path.name}: tf_codes в метаданных не совпадают с заголовками ###")
        if len(set(found)) != len(found):
            errors.append(f"{path.name}: повторяющиеся коды ТФ")
        for code in found:
            headers[code] = doc_id
        row = manifest.get(doc_id)
        if not row:
            errors.append(f"{path.name}: нет строки в manifest.csv")
        elif int(row["tf_count"]) != len(found):
            errors.append(f"{path.name}: tf_count {row['tf_count']} в манифесте, заголовков {len(found)}")
        if raw_dir:
            n = raw_tf_count(Path(raw_dir), meta["ps_code"].strip('"'))
            if n is not None and n != len(found):
                errors.append(f"{path.name}: в оригинале {n} кодов ТФ, в .md {len(found)}")
        body = text.split("\n---\n", 1)[-1]
        for word in NOISE:
            if word in body:
                errors.append(f"{path.name}: мусор «{word}»")
        blocks = re.split(r"\n(?=#{2,4} )", body)
        for block in blocks:
            head, _, rest = block.partition("\n")
            if head.startswith("####") and not rest.strip():
                errors.append(f"{path.name}: пустой блок «{head}»")
        tfs = re.split(r"\n(?=### )", body)[1:]
        for tf in tfs:
            if "#### Трудовые действия" not in tf:
                warnings.append(f"{path.name}: {tf.splitlines()[0][4:60]} — нет трудовых действий")

    for row in csv.DictReader(open(competencies_path, encoding="utf-8")):
        key = competency_key(row)
        name = " ".join(row["name"].split())
        if key is None:
            errors.append(f"компетенция {row['code']} «{name}»: нет кода ТФ в competencies.csv ({row['ps_code']})")
        elif key not in headers:
            errors.append(f"компетенция {row['code']}: {key} не найдена в корпусе")

    for w in warnings:
        print("ПРЕДУПРЕЖДЕНИЕ:", w)
    for e in errors:
        print("ОШИБКА:", e)
    print(f"Документов: {len(list(Path(clean_dir).glob('*.md')))}, ТФ: {len(headers)}, "
          f"ошибок: {len(errors)}, предупреждений: {len(warnings)}")
    return 1 if errors else 0


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
