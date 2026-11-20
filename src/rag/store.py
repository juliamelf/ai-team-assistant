"""Хранилище фрагментов в PostgreSQL (pgvector)."""

from __future__ import annotations

from typing import Protocol

import psycopg
from psycopg.rows import dict_row

from src.rag.chunking import Chunk


class ChunkStore(Protocol):
    """Интерфейс хранилища фрагментов."""

    def get_hashes(self, doc_id: str) -> dict[str, str]:
        """Возвращает {chunk_id: content_hash} для документа."""

    def apply(
        self,
        doc_id: str,
        upserts: list[tuple[Chunk, list[float]]],
        delete_ids: list[str],
    ) -> None:
        """Атомарно вставляет/обновляет и удаляет фрагменты документа.

        Любое исключение из apply означает, что по документу ничего не изменено.
        """


class PgChunkStore:
    """Реализация ChunkStore для PostgreSQL."""

    _GET_HASHES_SQL = """
        SELECT chunk_id, content_hash
        FROM rag.chunks
        WHERE doc_id = %s
    """

    _UPSERT_SQL = """
        INSERT INTO rag.chunks
            (chunk_id, doc_id, ps_code, doc_type, doc_role, doc_version,
             chunk_type, otf_code, tf_code, qualification_level,
             heading, text, content_hash, embedding_model, embedding)
        VALUES
            (%s, %s, %s, %s, %s, %s,
             %s, %s, %s, %s,
             %s, %s, %s, %s, %s)
        ON CONFLICT (chunk_id) DO UPDATE SET
            ps_code         = EXCLUDED.ps_code,
            doc_type        = EXCLUDED.doc_type,
            doc_role        = EXCLUDED.doc_role,
            doc_version     = EXCLUDED.doc_version,
            chunk_type      = EXCLUDED.chunk_type,
            otf_code        = EXCLUDED.otf_code,
            tf_code         = EXCLUDED.tf_code,
            qualification_level = EXCLUDED.qualification_level,
            heading         = EXCLUDED.heading,
            text            = EXCLUDED.text,
            content_hash    = EXCLUDED.content_hash,
            embedding_model = EXCLUDED.embedding_model,
            embedding       = EXCLUDED.embedding,
            indexed_at      = now()
    """

    _DELETE_SQL = "DELETE FROM rag.chunks WHERE chunk_id = %s"

    _CHECK_TABLE_SQL = """
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'rag' AND table_name = 'chunks'
        )
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _ensure_table(self) -> None:
        """Проверяет, что таблица rag.chunks существует."""
        with self._conn.cursor() as cur:
            cur.execute(self._CHECK_TABLE_SQL)
            exists = cur.fetchone()[0]
        if not exists:
            raise psycopg.ProgrammingError(
                "Таблица rag.chunks не найдена. "
                "Примените миграцию db/migrations/08_rag_chunks.sql"
            )

    def get_hashes(self, doc_id: str) -> dict[str, str]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(self._GET_HASHES_SQL, (doc_id,))
            rows = cur.fetchall()
        return {row["chunk_id"]: row["content_hash"] for row in rows}

    def apply(
        self,
        doc_id: str,
        upserts: list[tuple[Chunk, list[float]]],
        delete_ids: list[str],
    ) -> None:
        self._ensure_table()

        with self._conn.cursor() as cur:
            # Вставляем / обновляем
            for chunk, embedding in upserts:
                cur.execute(self._UPSERT_SQL, (
                    chunk.chunk_id,
                    chunk.doc_id,
                    chunk.ps_code,
                    chunk.doc_type,
                    chunk.doc_role,
                    chunk.doc_version,
                    chunk.chunk_type,
                    chunk.otf_code,
                    chunk.tf_code,
                    chunk.qualification_level,
                    chunk.heading,
                    chunk.text,
                    chunk.content_hash,
                    "ai-sage/Giga-Embeddings-instruct-3B",
                    embedding,
                ))

            # Удаляем лишние
            for chunk_id in delete_ids:
                cur.execute(self._DELETE_SQL, (chunk_id,))

        # Если дошли сюда — транзакция коммитится автоматически
        # (psycopg 3 auto-commit off по умолчанию, но connection.commit() вызывается
        #  при выходе из контекста, если не было исключения)
