"""Прогон по корпусу и командная строка."""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from src.rag.chunking import (
    Chunk,
    CorpusFormatError,
    EMBEDDING_MODEL,
    split_document,
)
from src.rag.embeddings import EmbeddingError, embed_with_retry, make_embedder
from src.rag.store import ChunkStore, PgChunkStore

EMBEDDING_DIM = 2048
BATCH_SIZE = 16


@dataclass(frozen=True)
class IndexReport:
    """Отчёт о прогоне индексации."""

    docs_total: int = 0
    docs_ok: int = 0
    docs_failed: int = 0
    chunks_total: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    errors: list[str] = field(default_factory=list)
    stopped: bool = False


def index_corpus(
    clean_dir: Path,
    embed: Callable[[list[str]], list[list[float]]] | None,
    store: ChunkStore | None,
    *,
    doc_id: str | None = None,
    dry_run: bool = False,
    batch_size: int = BATCH_SIZE,
    sleep: Callable[[float], None] = time.sleep,
) -> IndexReport:
    """Индексирует документы из папки корпуса.

    При dry_run=True embed и store не вызываются и могут быть None.
    Проверка doc_id против имени файла выполняется здесь.
    """
    # Собираем список файлов
    md_files = sorted(clean_dir.glob("*.md"))
    if not md_files:
        print(f"ошибка: в {clean_dir} нет *.md файлов", file=sys.stderr)
        return IndexReport(
            docs_total=0,
            errors=[f"{clean_dir}: нет *.md файлов"],
        )

    # Фильтруем по doc_id если задан
    if doc_id is not None:
        target = clean_dir / f"{doc_id}.md"
        if not target.exists():
            print(
                f"ошибка: файл {doc_id}.md не найден в {clean_dir}",
                file=sys.stderr,
            )
            return IndexReport(
                docs_total=1,
                errors=[f"{doc_id}.md: не найден в {clean_dir}"],
            )
        md_files = [target]

    docs_total = len(md_files)
    all_chunks_total = 0
    all_inserted = 0
    all_updated = 0
    all_unchanged = 0
    all_deleted = 0
    docs_ok = 0
    docs_failed = 0
    errors: list[str] = []

    for md_file in md_files:
        filename = md_file.name
        file_doc_id = md_file.stem  # имя файла без .md

        # Читаем файл
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception as exc:
            msg = f"{filename}: ошибка чтения: {exc}"
            errors.append(msg)
            docs_failed += 1
            continue

        # Проверяем doc_id из метаданных против имени файла
        try:
            from src.rag.chunking import parse_front_matter

            meta = parse_front_matter(text)
            meta_doc_id = str(meta.get("doc_id", ""))
        except CorpusFormatError:
            # parse_front_matter уже выбросила нужную ошибку ниже
            meta_doc_id = ""

        if meta_doc_id and meta_doc_id != file_doc_id:
            msg = (
                f"{filename}: doc_id {meta_doc_id} "
                f"не совпадает с именем файла"
            )
            errors.append(msg)
            docs_failed += 1
            continue

        # Разбираем и нарезаем
        try:
            chunks = split_document(
                text,
                filename=filename,
                embedding_model=EMBEDDING_MODEL,
            )
        except CorpusFormatError as exc:
            msg = f"{filename}: {exc}"
            errors.append(msg)
            docs_failed += 1
            continue

        if not chunks:
            msg = f"{filename}: нет фрагментов"
            errors.append(msg)
            docs_failed += 1
            continue

        # --- dry_run ---
        if dry_run:
            n_doc = sum(1 for c in chunks if c.chunk_type == "doc")
            n_otf = sum(1 for c in chunks if c.chunk_type == "otf")
            n_tf = sum(1 for c in chunks if c.chunk_type == "tf")
            line = (
                f"{filename}: фрагментов {len(chunks)} "
                f"(doc={n_doc}, otf={n_otf}, tf={n_tf}), "
                f"новых 0, изменено 0, без изменений 0, удалено 0"
            )
            print(line)
            all_chunks_total += len(chunks)
            docs_ok += 1
            continue

        # --- Работа с хранилищем ---
        assert store is not None, "store не может быть None не в dry_run режиме"

        existing_hashes = store.get_hashes(file_doc_id)
        existing_set = set(existing_hashes.keys())
        current_ids = {c.chunk_id for c in chunks}

        # Фрагменты к удалению: есть в БД, но нет в документе
        delete_ids = sorted(existing_set - current_ids)

        # Классифицируем: новые, изменённые, без изменений
        new_chunks: list[Chunk] = []
        changed_chunks: list[Chunk] = []
        unchanged_count = 0

        for chunk in chunks:
            if chunk.chunk_id not in existing_set:
                new_chunks.append(chunk)
            elif existing_hashes[chunk.chunk_id] != chunk.content_hash:
                changed_chunks.append(chunk)
            else:
                unchanged_count += 1

        # --- Эмбеддинги ---
        upserts: list[tuple[Chunk, list[float]]] = []
        embed_texts: list[str] = []

        for chunk in new_chunks + changed_chunks:
            embed_texts.append(chunk.text)

        # Разбиваем на батчи
        total_new_changed = len(embed_texts)
        batch_idx = 0
        embedding_errors: list[str] = []
        stopped = False

        while batch_idx * batch_size < total_new_changed:
            batch_texts = embed_texts[
                batch_idx * batch_size : (batch_idx + 1) * batch_size
            ]
            batch_chunks = (new_chunks + changed_chunks)[
                batch_idx * batch_size : (batch_idx + 1) * batch_size
            ]

            try:
                vectors = embed_with_retry(
                    embed, batch_texts, max_retries=3, sleep=sleep
                )
            except EmbeddingError as exc:
                if not exc.retryable:
                    errors.append(
                        f"{filename}: ошибка эмбеддинга: {exc}"
                    )
                    docs_failed += 1
                    stopped = True
                    # Добавляем все необработанные документы в docs_failed
                    remaining = [
                        f.name
                        for f in md_files[
                            md_files.index(md_file) + 1 :
                        ]
                    ]
                    if remaining:
                        errors.append(
                            f"остановлено: не обработаны {', '.join(remaining)}"
                        )
                    break
                embedding_errors.append(
                    f"{filename}: ошибка эмбеддинга: {exc}"
                )
                break

            # Проверяем векторы
            if len(vectors) != len(batch_texts):
                embedding_errors.append(
                    f"{filename}: число векторов {len(vectors)} "
                    f"не совпадает с числом текстов {len(batch_texts)}"
                )
                break

            for vec in vectors:
                if len(vec) != EMBEDDING_DIM:
                    embedding_errors.append(
                        f"{filename}: вектор длины {len(vec)}, "
                        f"ожидалось {EMBEDDING_DIM}"
                    )
                    break
            else:
                for chunk, vec in zip(batch_chunks, vectors):
                    upserts.append((chunk, vec))
                batch_idx += 1
                continue

            break  # выходим из цикла if была ошибка проверки

        if stopped:
            break

        if embedding_errors:
            errors.extend(embedding_errors)
            docs_failed += 1
            continue

        # --- Запись в БД ---
        try:
            store.apply(file_doc_id, upserts, delete_ids)
        except Exception as exc:
            msg = f"{filename}: ошибка записи в БД: {exc}"
            errors.append(msg)
            docs_failed += 1
            continue

        # --- Отчёт по документу ---
        n_inserted = len(new_chunks)
        n_updated = len(changed_chunks)
        n_deleted = len(delete_ids)

        all_inserted += n_inserted
        all_updated += n_updated
        all_unchanged += unchanged_count
        all_deleted += n_deleted
        all_chunks_total += len(chunks)
        docs_ok += 1

        line = (
            f"{filename}: фрагментов {len(chunks)}, "
            f"новых {n_inserted}, изменено {n_updated}, "
            f"без изменений {unchanged_count}, удалено {n_deleted}"
        )
        print(line)

    # --- Итоговый отчёт ---
    report = IndexReport(
        docs_total=docs_total,
        docs_ok=docs_ok,
        docs_failed=docs_failed,
        chunks_total=all_chunks_total,
        inserted=all_inserted,
        updated=all_updated,
        unchanged=all_unchanged,
        deleted=all_deleted,
        errors=errors,
        stopped=stopped,
    )

    # Итоговая строка
    summary_parts = [
        f"итог: документов {docs_total}, "
        f"успешно {docs_ok}, с ошибками {docs_failed}; "
        f"фрагментов всего {all_chunks_total}, "
        f"новых {all_inserted}, изменено {all_updated}, "
        f"без изменений {all_unchanged}, удалено {all_deleted}",
    ]
    if errors:
        summary_parts.append("ошибки:")
        summary_parts.extend(errors)

    print("\n".join(summary_parts))

    return report


def _check_env() -> list[str]:
    """Проверяет переменные окружения. Возвращает список ошибок."""
    errors: list[str] = []
    if not os.environ.get("CLOUDRU_API_KEY", "").strip():
        errors.append("не задана CLOUDRU_API_KEY")
    for var in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        if not os.environ.get(var, "").strip():
            errors.append(f"не задана {var}")
    return errors


def main(argv: list[str] | None = None) -> int:
    """Точка входа для командной строки. Возвращает код выхода."""
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Индексация корпуса профстандартов в pgvector"
    )
    parser.add_argument(
        "clean_dir",
        type=Path,
        help="Путь к папке с очищенными *.md файлами",
    )
    parser.add_argument(
        "--doc",
        type=str,
        default=None,
        help="Индексировать только указанный doc_id",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Только разбор и отчёт, без API и БД",
    )

    args = parser.parse_args(argv)

    # Проверяем папку
    if not args.clean_dir.is_dir():
        print(f"ошибка: {args.clean_dir} — не директория", file=sys.stderr)
        return 2

    # Проверяем *.md в папке
    md_files = list(args.clean_dir.glob("*.md"))
    if not md_files:
        print(
            f"ошибка: в {args.clean_dir} нет *.md файлов",
            file=sys.stderr,
        )
        return 2

    # Проверяем --doc
    if args.doc is not None:
        target = args.clean_dir / f"{args.doc}.md"
        if not target.exists():
            print(
                f"ошибка: файл {args.doc}.md не найден в {args.clean_dir}",
                file=sys.stderr,
            )
            return 2

    # Проверяем окружение (если не dry_run)
    if not args.dry_run:
        env_errors = _check_env()
        if env_errors:
            for e in env_errors:
                print(f"ошибка окружения: {e}", file=sys.stderr)
            return 2

        # Проверяем таблицу rag.chunks
        try:
            import psycopg

            conn = psycopg.connect(
                host=os.environ["POSTGRES_HOST"],
                port=int(os.environ["POSTGRES_PORT"]),
                dbname=os.environ["POSTGRES_DB"],
                user=os.environ["POSTGRES_USER"],
                password=os.environ["POSTGRES_PASSWORD"],
            )
            try:
                PgChunkStore.ensure_table(conn)
            except Exception:
                conn.close()
                raise
        except Exception as exc:
            print(f"ошибка подключения к БД: {exc}", file=sys.stderr)
            return 2

        # Определяем embed и store
        try:
            embed = make_embedder()
        except EmbeddingError as exc:
            print(f"ошибка инициализации embed: {exc}", file=sys.stderr)
            return 2

        store = PgChunkStore(
            conn, embedding_model=EMBEDDING_MODEL
        )
    else:
        embed: Callable[[list[str]], list[list[float]]] | None = None
        store: ChunkStore | None = None
        conn = None

    try:
        report = index_corpus(
            args.clean_dir,
            embed,
            store,
            doc_id=args.doc,
            dry_run=args.dry_run,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # Код выхода
    if report.stopped:
        return 1
    if report.errors:
        return 1
    return 0
