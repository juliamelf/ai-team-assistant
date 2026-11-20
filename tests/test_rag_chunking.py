from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.rag.chunking import (
    MAX_CHUNK_CHARS,
    CorpusFormatError,
    content_hash,
    parse_front_matter,
    split_document,
)

MODEL = "ai-sage/Giga-Embeddings-instruct-3B"
ROOT = Path(__file__).resolve().parents[1]
REAL_DOC = ROOT / "data" / "clean" / "ps-06.004.md"
MINI_DOC = Path(__file__).resolve().parent / "data" / "mini" / "ps-00.001.md"

VALID_FIELDS = {
    "doc_id": "ps-00.001",
    "ps_code": '"00.001"',
    "title": "Выдуманный профстандарт",
    "doc_type": "profstandard",
    "doc_role": "source",
    "version": "выдуманный приказ от 1 января 2020 г. № 1",
    "tf_codes": '["00.001:A/01.4"]',
}

VALID_BODY = """# Профстандарт 00.001 «Выдуманный профстандарт»

Вид профессиональной деятельности: Выдуманная деятельность.

## 00.001:A — Выдуманная обобщённая функция (уровень квалификации 4)

Требования к образованию и обучению: Выдуманное образование.

### 00.001:A/01.4 — Выдуманная трудовая функция

Обобщённая трудовая функция: 00.001:A — Выдуманная обобщённая функция. Уровень квалификации 4.

#### Трудовые действия
- Выдуманное действие.
"""


def make_document(fields: dict[str, str], body: str) -> str:
    lines = ["---"]
    lines.extend(f"{key}: {value}" for key, value in fields.items())
    lines.extend(["---", "", body])
    return "\n".join(lines)


def test_ac1_real_profstandard_slices_into_expected_chunks():
    # подготовка
    text = REAL_DOC.read_text(encoding="utf-8")

    # действие
    chunks = split_document(text, MODEL)

    # проверка
    types = [chunk.chunk_type for chunk in chunks]
    assert types.count("doc") == 1
    assert types.count("otf") == 4
    assert types.count("tf") == 20
    assert len(chunks) == 25
    assert chunks[0].chunk_type == "doc"

    tf_codes = [chunk.tf_code for chunk in chunks if chunk.chunk_type == "tf"]
    assert tf_codes == parse_front_matter(text)["tf_codes"]
    assert len(tf_codes) == len(set(tf_codes)) == 20


def test_ac2_mini_document_has_correct_metadata_fields():
    # подготовка
    text = MINI_DOC.read_text(encoding="utf-8")

    # действие
    chunks = split_document(text, MODEL)

    # проверка
    assert [chunk.chunk_type for chunk in chunks] == [
        "doc",
        "otf",
        "tf",
        "tf",
        "otf",
        "tf",
    ]
    doc, otf_a, tf_a1, tf_a2, otf_b, tf_b1 = chunks

    assert parse_front_matter(text)["tf_codes"] == [
        "00.001:A/01.4",
        "00.001:A/02.4",
        "00.001:B/01.5",
    ]

    assert doc.chunk_id == "ps-00.001#doc"
    assert doc.otf_code is None
    assert doc.tf_code is None
    assert doc.qualification_level is None
    assert doc.heading == "Профстандарт 00.001 «Выдуманный профстандарт»"

    assert otf_a.chunk_id == "ps-00.001#00.001:A"
    assert otf_a.otf_code == "00.001:A"
    assert otf_a.tf_code is None
    assert otf_a.qualification_level == 4
    assert otf_a.heading == (
        "00.001:A — Выдуманная обобщённая функция А (уровень квалификации 4)"
    )

    assert tf_a1.chunk_id == "ps-00.001#00.001:A/01.4"
    assert tf_a1.otf_code == "00.001:A"
    assert tf_a1.tf_code == "00.001:A/01.4"
    assert tf_a1.qualification_level == 4
    assert tf_a1.heading == "00.001:A/01.4 — Выдуманная трудовая функция один"

    assert tf_a2.chunk_id == "ps-00.001#00.001:A/02.4"
    assert tf_a2.otf_code == "00.001:A"
    assert tf_a2.tf_code == "00.001:A/02.4"
    assert tf_a2.qualification_level == 4
    assert tf_a2.heading == "00.001:A/02.4 — Выдуманная трудовая функция два"

    assert otf_b.chunk_id == "ps-00.001#00.001:B"
    assert otf_b.otf_code == "00.001:B"
    assert otf_b.tf_code is None
    assert otf_b.qualification_level == 5
    assert otf_b.heading == (
        "00.001:B — Выдуманная обобщённая функция Б (уровень квалификации 5)"
    )

    assert tf_b1.chunk_id == "ps-00.001#00.001:B/01.5"
    assert tf_b1.otf_code == "00.001:B"
    assert tf_b1.tf_code == "00.001:B/01.5"
    assert tf_b1.qualification_level == 5
    assert tf_b1.heading == "00.001:B/01.5 — Выдуманная трудовая функция три"

    for chunk in chunks:
        assert chunk.doc_id == "ps-00.001"
        assert chunk.ps_code == "00.001"
        assert chunk.doc_type == "profstandard"
        assert chunk.doc_role == "source"
        assert chunk.doc_version == "выдуманный приказ от 1 января 2020 г. № 1"
        assert chunk.content_hash == content_hash(chunk.text, MODEL)


def test_ac3_embedding_texts_have_prefix_heading_and_subsections():
    # подготовка
    text = MINI_DOC.read_text(encoding="utf-8")

    # действие
    chunks = split_document(text, MODEL)

    # проверка
    doc, otf_a, tf_a1, tf_a2, otf_b, tf_b1 = chunks
    assert doc.text.startswith("# Профстандарт 00.001")

    prefix = "Профстандарт 00.001 «Выдуманный профстандарт»"
    for chunk in (otf_a, tf_a1, tf_a2, otf_b, tf_b1):
        assert chunk.text.startswith(prefix + "\n\n")
        assert chunk.heading in chunk.text

    for subsection in (
        "#### Трудовые действия",
        "#### Необходимые умения",
        "#### Необходимые знания",
    ):
        assert subsection in tf_a1.text

    assert tf_a2.heading not in otf_a.text
    assert otf_b.heading not in tf_a2.text


def test_ac4_content_hash_depends_on_text_and_model():
    # подготовка
    text = "Выдуманный текст для хэша"
    expected = hashlib.sha256(f"{MODEL}\n{text}".encode("utf-8")).hexdigest()

    # действие
    same = content_hash(text, MODEL)
    same_again = content_hash(text, MODEL)
    other_text = content_hash(text + "!", MODEL)
    other_model = content_hash(text, "другая-модель")

    # проверка
    assert same == expected
    assert same_again == expected
    assert other_text != expected
    assert other_model != expected


def test_ac11_parse_fails_without_front_matter():
    # подготовка
    text = "# Профстандарт без метаданных\n\nТекст документа.\n"

    # действие и проверка
    with pytest.raises(CorpusFormatError):
        parse_front_matter(text)


def test_ac11_parse_fails_when_required_field_missing():
    # подготовка
    fields = dict(VALID_FIELDS)
    del fields["title"]

    # действие и проверка
    with pytest.raises(CorpusFormatError):
        parse_front_matter(make_document(fields, VALID_BODY))


def test_ac11_parse_fails_when_required_field_is_empty():
    # подготовка
    fields = dict(VALID_FIELDS)
    fields["tf_codes"] = "[]"

    # действие и проверка
    with pytest.raises(CorpusFormatError):
        parse_front_matter(make_document(fields, VALID_BODY))


def test_ac11_codes_mismatch_raises_corpus_format_error():
    # подготовка
    fields = dict(VALID_FIELDS)
    fields["tf_codes"] = '["00.001:A/02.4"]'

    # действие и проверка
    with pytest.raises(CorpusFormatError):
        split_document(make_document(fields, VALID_BODY), MODEL)


def test_ac11_duplicate_tf_codes_raise_corpus_format_error():
    # подготовка
    fields = dict(VALID_FIELDS)
    fields["tf_codes"] = '["00.001:A/01.4", "00.001:A/01.4"]'
    body = VALID_BODY + """
### 00.001:A/01.4 — Выдуманная трудовая функция (повтор)

#### Трудовые действия
- Выдуманное повторное действие.
"""

    # действие и проверка
    with pytest.raises(CorpusFormatError):
        split_document(make_document(fields, body), MODEL)


def test_ac11_empty_file_raises():
    # подготовка
    text = ""

    # действие и проверка
    with pytest.raises(CorpusFormatError):
        split_document(text, MODEL)


def test_ac11_no_tf_heading_raises():
    # подготовка
    body = """# Профстандарт 00.001 «Выдуманный профстандарт»

## 00.001:A — Выдуманная обобщённая функция (уровень квалификации 4)

Требования к образованию и обучению: Выдуманное образование.
"""

    # действие и проверка
    with pytest.raises(CorpusFormatError):
        split_document(make_document(VALID_FIELDS, body), MODEL)


def test_ac11_fragment_longer_than_limit_raises():
    # подготовка
    assert MAX_CHUNK_CHARS == 12000
    body = VALID_BODY.replace(
        "- Выдуманное действие.",
        "- " + "а" * (MAX_CHUNK_CHARS + 1),
    )

    # действие и проверка
    with pytest.raises(CorpusFormatError):
        split_document(make_document(VALID_FIELDS, body), MODEL)


def test_ac11_tf_heading_before_otf_raises():
    # подготовка
    body = """# Профстандарт 00.001 «Выдуманный профстандарт»

### 00.001:A/01.4 — Выдуманная трудовая функция

#### Трудовые действия
- Выдуманное действие.

## 00.001:A — Выдуманная обобщённая функция (уровень квалификации 4)

Требования к образованию и обучению: Выдуманное образование.
"""

    # действие и проверка
    with pytest.raises(CorpusFormatError):
        split_document(make_document(VALID_FIELDS, body), MODEL)
