"""Разбор документа корпуса и нарезка на фрагменты."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


MAX_CHUNK_CHARS = 12000
EMBEDDING_MODEL = "ai-sage/Giga-Embeddings-instruct-3B"

_REQUIRED_FIELDS = (
    "doc_id",
    "ps_code",
    "title",
    "doc_type",
    "doc_role",
    "version",
    "tf_codes",
)

# Код ТФ: A/01.4
_RE_TF_CODE = re.compile(r"^([A-Z]/\d{2}\.\d)")

# Код ОТФ из заголовка ##: 06.004:A
_RE_OTF_CODE = re.compile(r"^(\d{2}\.\d{3}:[A-Z])")

# Уровень квалификации из "(уровень квалификации N)"
_RE_LEVEL_OTF = re.compile(r"уровень\s+квалификации\s+(\d+)", re.IGNORECASE)

# Код ТФ из заголовка ###: 06.004:A/01.4
_RE_TF_FULL = re.compile(r"^(\d{2}\.\d{3}:[A-Z]/\d{2}\.\d)")


class CorpusFormatError(ValueError):
    """Ошибка формата документа корпуса."""


@dataclass(frozen=True)
class Chunk:
    """Фрагмент документа."""

    chunk_id: str
    doc_id: str
    ps_code: str
    doc_type: str
    doc_role: str
    doc_version: str
    chunk_type: str  # "doc" | "otf" | "tf"
    otf_code: str | None
    tf_code: str | None
    qualification_level: int | None
    heading: str
    text: str
    content_hash: str


def content_hash(text: str, embedding_model: str) -> str:
    """sha256 hex от строки '<model>\\n<text>'."""
    raw = f"{embedding_model}\n{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_tf_codes_list(raw: str) -> list[str]:
    """Парсит tf_codes из YAML-строки вида ['a', 'b'] или [\"a\", \"b\"]."""
    result: list[str] = []
    # Ищем содержимое между [ и ]
    m = re.search(r"\[(.*)\]", raw, re.DOTALL)
    if m is None:
        return []
    inner = m.group(1)
    # Извлекаем все строки в двойных кавычках
    for item in re.findall(r'"([^"]*)"', inner):
        result.append(item)
    return result


def parse_front_matter(text: str) -> dict[str, Any]:
    """Разбирает YAML-блок между --- в начале файла.

    Возвращает словарь полей. tf_codes — list[str].
    Выбрасывает CorpusFormatError, если блока нет или пустое обязательное поле.
    """
    stripped = text.lstrip("\ufeff")  # убираем BOM если есть

    if not stripped.startswith("---"):
        raise CorpusFormatError("нет блока метаданных")

    end_idx = stripped.find("---", 3)
    if end_idx < 0:
        raise CorpusFormatError("нет закрывающего ---")

    yaml_block = stripped[3:end_idx].strip()
    body = stripped[end_idx + 3:]

    if not yaml_block:
        raise CorpusFormatError("пустой блок метаданных")

    # Простой парсинг YAML: ключ: значение
    fields: dict[str, Any] = {}
    tf_codes_raw: str | None = None

    for line in yaml_block.splitlines():
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue
        colon_idx = line_stripped.find(":")
        if colon_idx < 0:
            continue
        key = line_stripped[:colon_idx].strip()
        value = line_stripped[colon_idx + 1:].strip()
        if key == "tf_codes":
            tf_codes_raw = value
        else:
            # Убираем кавычки вокруг значения YAML (AC-2)
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                value = value[1:-1]
            fields[key] = value

    # tf_codes — список строк
    if tf_codes_raw is not None:
        fields["tf_codes"] = _parse_tf_codes_list(tf_codes_raw)
    elif "tf_codes" not in fields:
        fields["tf_codes"] = []

    # Проверяем обязательные поля
    for field in _REQUIRED_FIELDS:
        val = fields.get(field)
        if val is None or (isinstance(val, str) and val.strip() == ""):
            raise CorpusFormatError(f"пустое поле {field}")
        if field == "tf_codes" and isinstance(val, list) and len(val) == 0:
            raise CorpusFormatError("пустое поле tf_codes")

    return fields


def _extract_heading_text(heading_line: str) -> str:
    """Извлекает текст заголовка без маркеров # и пробелов по краям."""
    return re.sub(r"^#+\s+", "", heading_line).strip()


def _extract_tf_code_from_h3(heading_text: str) -> str | None:
    """Извлекает код ТФ (например A/01.4) из текста заголовка ###."""
    m = _RE_TF_CODE.search(heading_text)
    return m.group(1) if m else None


def _extract_qualification_level_from_h3(tf_code: str) -> int:
    """Извлекает уровень квалификации из кода ТФ (A/01.4 -> 4)."""
    parts = tf_code.split("/")
    if len(parts) < 2:
        return 0
    last_part = parts[-1]  # 01.4
    dot_idx = last_part.rfind(".")
    if dot_idx >= 0:
        return int(last_part[dot_idx + 1:])
    return 0


def _extract_qualification_level_from_h2(heading_text: str) -> int | None:
    """Извлекает уровень квалификации из заголовка ##."""
    m = _RE_LEVEL_OTF.search(heading_text)
    if m:
        return int(m.group(1))
    return None


def _build_chunk_text(
    chunk_type: str,
    raw_text: str,
    ps_code: str,
    title: str,
) -> str:
    """Собирает текст для эмбеддинга (раздел 5, шаг 6).

    doc: текст как есть, без пустых строк в конце.
    otf/tf: «Профстандарт <ps_code> «<title>»», пустая строка,
            затем текст фрагмента, без пустых строк в конце.
    """
    if chunk_type == "doc":
        return raw_text.rstrip("\n")

    prefix = f"Профстандарт {ps_code} \u00ab{title}\u00bb"
    return f"{prefix}\n\n{raw_text}".rstrip("\n")


def split_document(
    text: str,
    embedding_model: str = EMBEDDING_MODEL,
) -> list[Chunk]:
    """Разрезает текст документа на фрагменты.

    Порядок: doc, затем для каждой ОТФ — otf и её tf.
    Выбрасывает CorpusFormatError при нарушениях формата.
    """
    meta = parse_front_matter(text)
    body = text.lstrip("\ufeff")

    # Находим конец YAML блока
    body_start = body.find("---", 3)
    if body_start < 0:
        raise CorpusFormatError("нет закрывающего ---")
    body = body[body_start + 3:].lstrip("\n")

    doc_id: str = str(meta["doc_id"])
    ps_code: str = str(meta["ps_code"])
    doc_type: str = str(meta["doc_type"])
    doc_role: str = str(meta["doc_role"])
    doc_version: str = str(meta["version"])
    title: str = str(meta["title"])
    tf_codes_meta: list[str] = meta["tf_codes"]  # type: ignore[assignment]

    # Парсим все заголовки с их позициями и текстом
    # Каждая запись: (level, heading_line, heading_text, body_start_pos)
    headings: list[tuple[int, str, str, int]] = []
    for m in re.finditer(r"^(#{1,6})\s+(.+)$", body, re.MULTILINE):
        level = len(m.group(1))
        heading_line = m.group(0)
        heading_text = _extract_heading_text(heading_line)
        headings.append((level, heading_line, heading_text, m.start()))

    if not headings:
        raise CorpusFormatError("нет заголовков")

    # Проверяем: ### не может быть до первого ##
    first_h2_idx = next((i for i, h in enumerate(headings) if h[0] == 2), None)
    if first_h2_idx is None:
        raise CorpusFormatError("нет заголовков ##")

    for i, (level, _, ht, _) in enumerate(headings):
        if level == 3 and i < first_h2_idx:
            raise CorpusFormatError("### встречается до первого ##")

    # Определяем границы фрагментов
    # doc: от начала body до первого ##
    # otf: от ## до первого ### под ним
    # tf: от ### до следующего ### или ##

    chunks: list[Chunk] = []

    # --- doc фрагмент ---
    # doc: текст от H1 до первого ##; если H1 нет — ошибка
    if headings[0][0] != 1:
        label = doc_id
        raise CorpusFormatError(f"{label}: нет заголовка # до первого ##")

    doc_raw = body[: headings[first_h2_idx][3]].rstrip("\n")
    doc_heading = _extract_heading_text(headings[0][1])
    doc_chunk_id = f"{doc_id}#doc"

    if len(doc_raw) > MAX_CHUNK_CHARS:
        raise CorpusFormatError(
            f"{doc_chunk_id}: длина {len(doc_raw)} > {MAX_CHUNK_CHARS}"
        )

    doc_text = _build_chunk_text("doc", doc_raw, ps_code, title)
    doc_content_h = content_hash(doc_text, embedding_model)
    chunks.append(Chunk(
        chunk_id=doc_chunk_id,
        doc_id=doc_id,
        ps_code=ps_code,
        doc_type=doc_type,
        doc_role=doc_role,
        doc_version=doc_version,
        chunk_type="doc",
        otf_code=None,
        tf_code=None,
        qualification_level=None,
        heading=doc_heading,
        text=doc_text,
        content_hash=doc_content_h,
    ))

    # --- Группируем заголовки по ОТФ ---
    # Каждая ОТФ: h2 + все h3 под ней до следующего h2 или конца
    otf_blocks: list[tuple[tuple[int, str, str, int], list[tuple[int, str, str, int]]]] = []
    current_otf_h2: tuple[int, str, str, int] | None = None
    current_h3s: list[tuple[int, str, str, int]] = []

    for h in headings:
        if h[0] == 2:
            if current_otf_h2 is not None:
                otf_blocks.append((current_otf_h2, current_h3s))
            current_otf_h2 = h
            current_h3s = []
        elif h[0] == 3 and current_otf_h2 is not None:
            current_h3s.append(h)
        elif h[0] == 1 and current_otf_h2 is None:
            # h1 перед первым h2 — пропускаем (это doc)
            pass

    if current_otf_h2 is not None:
        otf_blocks.append((current_otf_h2, current_h3s))

    # --- Создаём otf и tf фрагменты ---
    collected_tf_codes: list[str] = []
    seen_tf_codes: set[str] = set()

    for otf_h2, h3s in otf_blocks:
        otf_heading_line, otf_heading_text = otf_h2[1], otf_h2[2]
        otf_code_m = _RE_OTF_CODE.search(otf_heading_text)
        otf_code = otf_code_m.group(1) if otf_code_m else None
        if otf_code is None:
            label = doc_id
            raise CorpusFormatError(
                f"{label}: заголовок ## без кода ОТФ: {otf_heading_text}"
            )
        otf_level = _extract_qualification_level_from_h2(otf_heading_text)
        otf_chunk_id = f"{doc_id}#{otf_code}"

        # Определяем границы текста ОТФ: от ## до первого ### или до конца блока
        otf_body_start = otf_h2[3]
        if h3s:
            otf_body_end = h3s[0][3]
        else:
            # До следующего h2 или конца body
            next_h2 = next(
                (h for h in headings if h[0] == 2 and h[3] > otf_h2[3]),
                None,
            )
            otf_body_end = next_h2[3] if next_h2 else len(body)

        otf_raw = body[otf_body_start:otf_body_end]

        if len(otf_raw) > MAX_CHUNK_CHARS:
            raise CorpusFormatError(
                f"{otf_chunk_id}: длина {len(otf_raw)} > {MAX_CHUNK_CHARS}"
            )

        otf_text = _build_chunk_text("otf", otf_raw, ps_code, title)
        otf_content_h = content_hash(otf_text, embedding_model)
        chunks.append(Chunk(
            chunk_id=otf_chunk_id,
            doc_id=doc_id,
            ps_code=ps_code,
            doc_type=doc_type,
            doc_role=doc_role,
            doc_version=doc_version,
            chunk_type="otf",
            otf_code=otf_code,
            tf_code=None,
            qualification_level=otf_level,
            heading=otf_heading_text,
            text=otf_text,
            content_hash=otf_content_h,
        ))

        # --- TF фрагменты ---
        for idx, h3 in enumerate(h3s):
            h3_heading_line, h3_heading_text = h3[1], h3[2]
            tf_code_short = _extract_tf_code_from_h3(h3_heading_text)
            tf_code_full = _RE_TF_FULL.search(h3_heading_text)
            tf_code_full_str = tf_code_full.group(1) if tf_code_full else tf_code_short
            tf_level = _extract_qualification_level_from_h3(tf_code_full_str) if tf_code_full_str else None

            # Определяем границы текста ТФ
            tf_body_start = h3[3]
            if idx + 1 < len(h3s):
                tf_body_end = h3s[idx + 1][3]
            else:
                # До следующего h2 или конца body
                next_h2 = next(
                    (h for h in headings if h[0] == 2 and h[3] > h3[3]),
                    None,
                )
                tf_body_end = next_h2[3] if next_h2 else len(body)

            tf_raw = body[tf_body_start:tf_body_end].rstrip("\n")

            if tf_code_full_str is None:
                label = doc_id
                raise CorpusFormatError(
                    f"{label}: заголовок ### без кода ТФ: {h3_heading_text}"
                )

            tf_chunk_id = f"{doc_id}#{tf_code_full_str}"

            if len(tf_raw) > MAX_CHUNK_CHARS:
                raise CorpusFormatError(
                    f"{tf_chunk_id}: длина {len(tf_raw)} > {MAX_CHUNK_CHARS}"
                )

            if tf_code_full_str in seen_tf_codes:
                raise CorpusFormatError(
                    f"{tf_chunk_id}: повторяющийся код ТФ"
                )
            seen_tf_codes.add(tf_code_full_str)
            collected_tf_codes.append(tf_code_full_str)

            tf_text = _build_chunk_text("tf", tf_raw, ps_code, title)
            tf_content_h = content_hash(tf_text, embedding_model)
            chunks.append(Chunk(
                chunk_id=tf_chunk_id,
                doc_id=doc_id,
                ps_code=ps_code,
                doc_type=doc_type,
                doc_role=doc_role,
                doc_version=doc_version,
                chunk_type="tf",
                otf_code=otf_code,
                tf_code=tf_code_full_str,
                qualification_level=tf_level,
                heading=h3_heading_text,
                text=tf_text,
                content_hash=tf_content_h,
            ))

    # --- Проверки валидации ---
    if collected_tf_codes != tf_codes_meta:
        label = doc_id
        raise CorpusFormatError(
            f"{label}: коды ТФ из заголовков {collected_tf_codes} "
            f"не совпадают с tf_codes {tf_codes_meta}"
        )

    return chunks
