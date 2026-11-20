from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from src.rag.chunking import MAX_CHUNK_CHARS, split_document
from src.rag.embeddings import EmbeddingError
from src.rag.index import EMBEDDING_DIM, index_corpus, main

ROOT = Path(__file__).resolve().parents[1]
CLEAN_DIR = ROOT / "data" / "clean"
TEST_DATA = Path(__file__).resolve().parent / "data"
BASE_DOC = TEST_DATA / "mini" / "ps-00.001.md"
SECOND_DOC = TEST_DATA / "mini" / "ps-00.002.md"
CHANGED_DOC = TEST_DATA / "variants" / "ps-00.001_changed.md"
WITHOUT_TF_DOC = TEST_DATA / "variants" / "ps-00.001_without_tf.md"


class FakeStore:
    def __init__(self, state: dict[str, dict[str, str]] | None = None):
        self.state = {
            doc_id: dict(hashes) for doc_id, hashes in (state or {}).items()
        }
        self.get_hashes_calls: list[str] = []
        self.applies: list[tuple[str, list, list[str]]] = []
        self.upserted = 0
        self.deleted = 0

    def get_hashes(self, doc_id: str) -> dict[str, str]:
        self.get_hashes_calls.append(doc_id)
        return dict(self.state.get(doc_id, {}))

    def apply(self, doc_id: str, upserts: list, delete_ids: list[str]) -> None:
        self.applies.append((doc_id, list(upserts), list(delete_ids)))
        hashes = self.state.setdefault(doc_id, {})
        for chunk, _vector in upserts:
            hashes[chunk.chunk_id] = chunk.content_hash
        for chunk_id in delete_ids:
            hashes.pop(chunk_id, None)
        self.upserted += len(upserts)
        self.deleted += len(delete_ids)

    def reset_counters(self) -> None:
        self.get_hashes_calls.clear()
        self.applies.clear()
        self.upserted = 0
        self.deleted = 0


class ExplodingStore:
    def get_hashes(self, doc_id: str) -> dict[str, str]:
        raise AssertionError("хранилище не должно вызываться")

    def apply(self, doc_id: str, upserts: list, delete_ids: list[str]) -> None:
        raise AssertionError("хранилище не должно вызываться")


class FakeEmbed:
    def __init__(self, dim: int = EMBEDDING_DIM):
        self.dim = dim
        self.calls: list[list[str]] = []

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[0.0] * self.dim for _ in texts]


def exploding_embed(texts: list[str]) -> list[list[float]]:
    raise AssertionError("embed не должен вызываться")


def embedding_error(message: str, *, retryable: bool) -> EmbeddingError:
    error = EmbeddingError(message)
    error.retryable = retryable
    return error


def make_corpus(base: Path, *sources: Path) -> Path:
    corpus = base / "corpus"
    corpus.mkdir(parents=True)
    for source in sources:
        shutil.copyfile(source, corpus / source.name)
    return corpus


def write_corpus(base: Path, files: dict[str, str]) -> Path:
    corpus = base / "corpus"
    corpus.mkdir(parents=True)
    for name, text in files.items():
        (corpus / name).write_text(text, encoding="utf-8")
    return corpus


def build_valid_document(
    *,
    doc_id: str,
    ps_code: str,
    title: str = "Выдуманный профстандарт",
    doc_role: str = "source",
    otf_levels: list[int] | None = None,
    tf_per_otf: int = 1,
) -> str:
    levels = otf_levels or [4]
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    tf_codes: list[str] = []
    body = [
        f"# Профстандарт {ps_code} «{title}»",
        "",
        "Вид профессиональной деятельности: Выдуманная деятельность.",
        "",
        "Основная цель: Проверка нарезки документов.",
        "",
    ]

    for index, level in enumerate(levels):
        letter = letters[index]
        otf_code = f"{ps_code}:{letter}"
        body.extend(
            [
                f"## {otf_code} — Выдуманная обобщённая функция {letter} "
                f"(уровень квалификации {level})",
                "",
                "Возможные наименования должностей, профессий: Выдуманный специалист.",
                "",
                "Требования к образованию и обучению: Выдуманное образование.",
                "",
            ]
        )
        for number in range(1, tf_per_otf + 1):
            tf_code = f"{otf_code}/{number:02d}.{level}"
            tf_codes.append(tf_code)
            body.extend(
                [
                    f"### {tf_code} — Выдуманная трудовая функция {letter}{number}",
                    "",
                    f"Обобщённая трудовая функция: {otf_code} — Выдуманная обобщённая "
                    f"функция {letter}. Уровень квалификации {level}.",
                    "",
                    "#### Трудовые действия",
                    f"- Выдуманное действие {letter}{number}.",
                    "",
                    "#### Необходимые умения",
                    f"- Выдуманное умение {letter}{number}.",
                    "",
                    "#### Необходимые знания",
                    f"- Выдуманное знание {letter}{number}.",
                    "",
                ]
            )

    codes = ", ".join(f'"{code}"' for code in tf_codes)
    front = [
        "---",
        f"doc_id: {doc_id}",
        f'ps_code: "{ps_code}"',
        f"title: {title}",
        "doc_type: profstandard",
        f"doc_role: {doc_role}",
        "version: выдуманный приказ от 1 января 2020 г. № 1",
        f"tf_codes: [{codes}]",
        "---",
        "",
    ]
    return "\n".join(front + body)


def build_document_with_tf_codes(
    *,
    doc_id: str,
    ps_code: str,
    tf_codes: list[str],
    body: str,
) -> str:
    codes = ", ".join(f'"{code}"' for code in tf_codes)
    return f"""---
doc_id: {doc_id}
ps_code: "{ps_code}"
title: Выдуманный профстандарт
doc_type: profstandard
doc_role: source
version: выдуманный приказ от 1 января 2020 г. № 1
tf_codes: [{codes}]
---

{body}"""


def body_with_one_tf(ps_code: str, tf_code: str) -> str:
    otf_code = f"{ps_code}:A"
    return f"""# Профстандарт {ps_code} «Выдуманный профстандарт»

## {otf_code} — Выдуманная обобщённая функция (уровень квалификации 4)

Требования к образованию и обучению: Выдуманное образование.

### {tf_code} — Выдуманная трудовая функция

#### Трудовые действия
- Выдуманное действие.
"""


def test_ac5_dry_run_covers_whole_corpus_without_external_calls():
    # подготовка и действие
    report = index_corpus(CLEAN_DIR, exploding_embed, ExplodingStore(), dry_run=True)

    # проверка
    assert report.docs_total == 9
    assert report.docs_ok == 9
    assert report.docs_failed == 0
    assert report.errors == []
    assert report.chunks_total == 535
    assert report.inserted == 0
    assert report.updated == 0
    assert report.deleted == 0

    type_counts = {"doc": 0, "otf": 0, "tf": 0}
    for path in sorted(CLEAN_DIR.glob("*.md")):
        for chunk in split_document(path.read_text(encoding="utf-8")):
            type_counts[chunk.chunk_type] += 1
    assert type_counts == {"doc": 9, "otf": 35, "tf": 491}


def test_ac6_empty_store_indexes_all_chunks_in_batches_up_to_16(tmp_path: Path):
    # подготовка
    text = build_valid_document(
        doc_id="ps-01.001",
        ps_code="01.001",
        otf_levels=[4, 5, 6],
        tf_per_otf=5,
    )
    corpus = write_corpus(tmp_path, {"ps-01.001.md": text})
    store = FakeStore()
    embed = FakeEmbed()

    # действие
    report = index_corpus(corpus, embed, store)

    # проверка
    assert report.docs_ok == 1
    assert report.docs_failed == 0
    assert report.errors == []
    assert report.inserted == 19
    assert report.chunks_total == 19
    assert [len(call) for call in embed.calls] == [16, 3]
    assert store.upserted == 19

    for _doc_id, upserts, delete_ids in store.applies:
        assert delete_ids == []
        for _chunk, vector in upserts:
            assert len(vector) == EMBEDDING_DIM


def test_ac7_repeat_run_without_changes_makes_no_calls(tmp_path: Path):
    # подготовка
    corpus = make_corpus(tmp_path, BASE_DOC)
    store = FakeStore()
    first = index_corpus(corpus, FakeEmbed(), store)
    assert first.inserted == 6

    store.reset_counters()

    # действие
    report = index_corpus(corpus, exploding_embed, store)

    # проверка
    assert report.docs_ok == 1
    assert report.errors == []
    assert report.inserted == 0
    assert report.updated == 0
    assert report.unchanged == 6
    assert report.deleted == 0
    assert store.upserted == 0
    assert store.deleted == 0


def test_ac8_changed_tf_is_reembedded_and_updated(tmp_path: Path):
    # подготовка
    corpus_v1 = make_corpus(tmp_path / "v1", BASE_DOC)
    corpus_v2 = tmp_path / "v2" / "corpus"
    corpus_v2.mkdir(parents=True)
    shutil.copyfile(CHANGED_DOC, corpus_v2 / BASE_DOC.name)
    store = FakeStore()
    first = index_corpus(corpus_v1, FakeEmbed(), store)
    assert first.inserted == 6

    store.reset_counters()
    embed = FakeEmbed()

    # действие
    report = index_corpus(corpus_v2, embed, store)

    # проверка
    assert report.docs_ok == 1
    assert report.errors == []
    assert report.inserted == 0
    assert report.updated == 1
    assert report.unchanged == 5
    assert report.deleted == 0
    assert [len(call) for call in embed.calls] == [1]
    assert store.upserted == 1

    changed_chunk, _vector = store.applies[-1][1][0]
    assert changed_chunk.chunk_id == "ps-00.001#00.001:A/01.4"


def test_ac9_removed_tf_is_deleted(tmp_path: Path):
    # подготовка
    corpus_v1 = make_corpus(tmp_path / "v1", BASE_DOC)
    corpus_v2 = tmp_path / "v2" / "corpus"
    corpus_v2.mkdir(parents=True)
    shutil.copyfile(WITHOUT_TF_DOC, corpus_v2 / BASE_DOC.name)
    store = FakeStore()
    first = index_corpus(corpus_v1, FakeEmbed(), store)
    assert first.inserted == 6

    store.reset_counters()

    # действие
    report = index_corpus(corpus_v2, exploding_embed, store)

    # проверка
    assert report.docs_ok == 1
    assert report.errors == []
    assert report.inserted == 0
    assert report.updated == 0
    assert report.unchanged == 5
    assert report.deleted == 1
    assert store.deleted == 1
    assert store.applies[-1][2] == ["ps-00.001#00.001:A/02.4"]


def test_ac10_bad_document_skipped_and_valid_document_indexed(tmp_path: Path):
    # подготовка
    corpus = make_corpus(tmp_path, SECOND_DOC)
    (corpus / "broken.md").write_text(
        "# Документ без метаданных\n", encoding="utf-8"
    )
    store = FakeStore()

    # действие
    report = index_corpus(corpus, FakeEmbed(), store)

    # проверка
    assert report.docs_total == 2
    assert report.docs_ok == 1
    assert report.docs_failed == 1
    assert len(report.errors) == 1
    assert "broken.md" in report.errors[0]
    assert store.get_hashes_calls == ["ps-00.002"]
    assert store.upserted == 3


def test_ac10_main_returns_exit_code_1_on_document_error(tmp_path: Path):
    # подготовка
    corpus = make_corpus(tmp_path, SECOND_DOC)
    (corpus / "broken.md").write_text(
        "# Документ без метаданных\n", encoding="utf-8"
    )

    # действие
    code = main([str(corpus), "--dry-run"])

    # проверка
    assert code == 1


def test_ac11_codes_mismatch_document_skipped_without_store_call(tmp_path: Path):
    # подготовка
    text = build_document_with_tf_codes(
        doc_id="ps-09.001",
        ps_code="09.001",
        tf_codes=["09.001:A/02.4"],
        body=body_with_one_tf("09.001", "09.001:A/01.4"),
    )
    corpus = write_corpus(tmp_path, {"ps-09.001.md": text})
    store = FakeStore()

    # действие
    report = index_corpus(corpus, exploding_embed, store)

    # проверка
    assert report.docs_failed == 1
    assert report.errors
    assert store.get_hashes_calls == []
    assert store.applies == []


def test_ac11_too_long_document_skipped_without_store_call(tmp_path: Path):
    # подготовка
    body = body_with_one_tf("09.001", "09.001:A/01.4").replace(
        "- Выдуманное действие.",
        "- " + "а" * (MAX_CHUNK_CHARS + 1),
    )
    text = build_document_with_tf_codes(
        doc_id="ps-09.001",
        ps_code="09.001",
        tf_codes=["09.001:A/01.4"],
        body=body,
    )
    corpus = write_corpus(tmp_path, {"ps-09.001.md": text})
    store = FakeStore()

    # действие
    report = index_corpus(corpus, exploding_embed, store)

    # проверка
    assert report.docs_failed == 1
    assert report.errors
    assert store.get_hashes_calls == []
    assert store.applies == []


def test_ac11_tf_before_otf_document_skipped_without_store_call(tmp_path: Path):
    # подготовка
    body = """# Профстандарт 09.001 «Выдуманный профстандарт»

### 09.001:A/01.4 — Выдуманная трудовая функция

#### Трудовые действия
- Выдуманное действие.

## 09.001:A — Выдуманная обобщённая функция (уровень квалификации 4)

Требования к образованию и обучению: Выдуманное образование.
"""
    text = build_document_with_tf_codes(
        doc_id="ps-09.001",
        ps_code="09.001",
        tf_codes=["09.001:A/01.4"],
        body=body,
    )
    corpus = write_corpus(tmp_path, {"ps-09.001.md": text})
    store = FakeStore()

    # действие
    report = index_corpus(corpus, exploding_embed, store)

    # проверка
    assert report.docs_failed == 1
    assert report.errors
    assert store.get_hashes_calls == []
    assert store.applies == []


def test_ac12_temporary_embed_errors_are_retried_then_document_indexed(
    tmp_path: Path,
):
    # подготовка
    corpus = make_corpus(tmp_path, BASE_DOC)
    sleeps: list[float] = []

    class FlakyEmbed:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, texts: list[str]) -> list[list[float]]:
            self.calls += 1
            if self.calls <= 2:
                raise embedding_error("временная ошибка API", retryable=True)
            return [[0.0] * EMBEDDING_DIM for _ in texts]

    embed = FlakyEmbed()
    store = FakeStore()

    # действие
    report = index_corpus(corpus, embed, store, sleep=sleeps.append)

    # проверка
    assert report.docs_ok == 1
    assert report.docs_failed == 0
    assert report.errors == []
    assert report.inserted == 6
    assert embed.calls == 3
    assert sleeps == [2, 4]


def test_ac13_embed_fails_three_times_document_skipped_next_processed(
    tmp_path: Path,
):
    # подготовка
    corpus = make_corpus(tmp_path, BASE_DOC, SECOND_DOC)
    sleeps: list[float] = []

    class FailFirstDocumentEmbed:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, texts: list[str]) -> list[list[float]]:
            self.calls += 1
            if self.calls <= 3:
                raise embedding_error("временная ошибка API", retryable=True)
            return [[0.0] * EMBEDDING_DIM for _ in texts]

    embed = FailFirstDocumentEmbed()
    store = FakeStore()

    # действие
    report = index_corpus(corpus, embed, store, sleep=sleeps.append)

    # проверка
    assert report.docs_total == 2
    assert report.docs_failed == 1
    assert report.docs_ok == 1
    assert report.errors
    assert "ps-00.001.md" in report.errors[0]
    assert embed.calls == 4
    assert sleeps == [2, 4]
    assert store.state.get("ps-00.001", {}) == {}
    assert store.upserted == 3
    assert len(store.state.get("ps-00.002", {})) == 3


def test_ac14_wrong_vector_dimension_skips_document_without_writes(
    tmp_path: Path,
):
    # подготовка
    assert EMBEDDING_DIM == 2048
    corpus = make_corpus(tmp_path, BASE_DOC)
    store = FakeStore()

    # действие
    report = index_corpus(corpus, FakeEmbed(dim=1024), store)

    # проверка
    assert report.docs_failed == 1
    assert report.docs_ok == 0
    assert report.errors
    assert store.upserted == 0
    assert store.deleted == 0
    assert all(not upserts and not deletes for _, upserts, deletes in store.applies)


def test_ac15_auth_error_stops_processing_without_retries(tmp_path: Path):
    # подготовка
    corpus = make_corpus(tmp_path, BASE_DOC, SECOND_DOC)
    sleeps: list[float] = []

    class AuthFailEmbed:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, texts: list[str]) -> list[list[float]]:
            self.calls += 1
            raise embedding_error("ошибка авторизации", retryable=False)

    embed = AuthFailEmbed()
    store = FakeStore()

    # действие
    report = index_corpus(corpus, embed, store, sleep=sleeps.append)

    # проверка
    assert report.stopped is True
    assert report.docs_ok == 0
    assert report.docs_failed >= 1
    assert report.errors
    assert "ps-00.001.md" in " ".join(report.errors)
    assert embed.calls == 1
    assert sleeps == []
    assert store.get_hashes_calls == ["ps-00.001"]
    assert store.upserted == 0
    assert store.state.get("ps-00.002", {}) == {}


def test_ac16_api_key_never_appears_in_output(tmp_path: Path, capsys, monkeypatch):
    # подготовка
    secret = "sk-выдуманный-ключ-НЕ-ПОКАЗЫВАТЬ-123"
    monkeypatch.setenv("CLOUDRU_API_KEY", secret)
    corpus = make_corpus(tmp_path, SECOND_DOC)
    (corpus / "broken.md").write_text(
        "# Документ без метаданных\n", encoding="utf-8"
    )

    # действие и проверка: ошибка по документу
    assert main([str(corpus), "--dry-run"]) == 1
    first = capsys.readouterr()
    assert secret not in first.out
    assert secret not in first.err

    # действие и проверка: ошибка запуска
    assert main([str(tmp_path / "missing"), "--dry-run"]) == 2
    second = capsys.readouterr()
    assert secret not in second.out
    assert secret not in second.err


def test_edge_doc_id_mismatch_skips_document(tmp_path: Path):
    # подготовка
    text = build_valid_document(doc_id="ps-09.999", ps_code="09.001")
    corpus = write_corpus(tmp_path, {"ps-09.001.md": text})
    store = FakeStore()

    # действие
    report = index_corpus(corpus, exploding_embed, store)

    # проверка
    assert report.docs_failed == 1
    assert report.errors
    assert "ps-09.001.md" in report.errors[0]
    assert store.get_hashes_calls == []
    assert store.applies == []


def test_edge_vector_count_mismatch_skips_document(tmp_path: Path):
    # подготовка
    corpus = make_corpus(tmp_path, BASE_DOC)

    class ShortEmbed:
        def __call__(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * EMBEDDING_DIM for _ in texts[:-1]]

    store = FakeStore()

    # действие
    report = index_corpus(corpus, ShortEmbed(), store)

    # проверка
    assert report.docs_failed == 1
    assert report.errors
    assert store.upserted == 0
    assert store.deleted == 0


def test_edge_database_write_error_rolls_back_document_and_continues(
    tmp_path: Path,
):
    # подготовка
    corpus = make_corpus(tmp_path, BASE_DOC, SECOND_DOC)

    class FailingApplyStore(FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.failed_applies: list[str] = []

        def apply(self, doc_id: str, upserts: list, delete_ids: list[str]) -> None:
            if doc_id == "ps-00.001":
                self.failed_applies.append(doc_id)
                raise RuntimeError("выдуманная ошибка базы")
            super().apply(doc_id, upserts, delete_ids)

    store = FailingApplyStore()

    # действие
    report = index_corpus(corpus, FakeEmbed(), store)

    # проверка
    assert report.docs_failed == 1
    assert report.docs_ok == 1
    assert "ps-00.001.md" in " ".join(report.errors)
    assert store.failed_applies == ["ps-00.001"]
    assert "ps-00.001" not in store.state
    assert len(store.state.get("ps-00.002", {})) == 3


def test_edge_document_removed_from_corpus_is_not_touched(tmp_path: Path):
    # подготовка
    corpus = make_corpus(tmp_path, BASE_DOC)
    old_chunk_id = "ps-00.999#doc"
    old_hash = "старый-хэш"
    store = FakeStore(state={"ps-00.999": {old_chunk_id: old_hash}})

    # действие
    report = index_corpus(corpus, FakeEmbed(), store)

    # проверка
    assert report.docs_ok == 1
    assert store.get_hashes_calls == ["ps-00.001"]
    assert store.state["ps-00.999"] == {old_chunk_id: old_hash}
    assert all(doc_id != "ps-00.999" for doc_id, _, _ in store.applies)
    assert report.deleted == 0


def test_input_missing_corpus_dir_returns_2(tmp_path: Path):
    # подготовка
    missing = tmp_path / "missing"

    # действие
    code = main([str(missing), "--dry-run"])

    # проверка
    assert code == 2


def test_input_corpus_without_markdown_returns_2(tmp_path: Path):
    # подготовка
    corpus = tmp_path / "empty"
    corpus.mkdir()

    # действие
    code = main([str(corpus), "--dry-run"])

    # проверка
    assert code == 2


def test_input_unknown_doc_filter_returns_2(tmp_path: Path):
    # подготовка
    corpus = make_corpus(tmp_path, BASE_DOC)

    # действие
    code = main([str(corpus), "--doc", "ps-99.999", "--dry-run"])

    # проверка
    assert code == 2


@pytest.mark.integration
def test_ac18_full_corpus_index_and_repeat_run_is_noop():
    # подготовка
    psycopg = pytest.importorskip("psycopg")
    load_dotenv = pytest.importorskip("dotenv").load_dotenv
    load_dotenv()

    from src.rag.embeddings import make_embedder
    from src.rag.store import PgChunkStore

    conn = psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
    try:
        embed = make_embedder()
        store = PgChunkStore(conn)

        # действие: полный запуск по реальному корпусу
        report = index_corpus(CLEAN_DIR, embed, store)

        # проверка: отчёт и содержимое таблицы
        assert report.errors == []
        assert report.chunks_total == 535

        check_conn = psycopg.connect(
            host=os.environ["POSTGRES_HOST"],
            port=os.environ.get("POSTGRES_PORT", "5432"),
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
        )
        try:
            with check_conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM rag.chunks")
                assert cur.fetchone()[0] == 535

                cur.execute(
                    "SELECT count(*) FROM rag.chunks WHERE vector_dims(embedding) <> 2048"
                )
                assert cur.fetchone()[0] == 0

                cur.execute(
                    "SELECT chunk_type, count(*) FROM rag.chunks GROUP BY chunk_type"
                )
                assert dict(cur.fetchall()) == {"doc": 9, "otf": 35, "tf": 491}

                cur.execute(
                    "SELECT doc_role, count(DISTINCT doc_id) FROM rag.chunks "
                    "GROUP BY doc_role"
                )
                assert dict(cur.fetchall()) == {"source": 2, "distractor": 7}
        finally:
            check_conn.close()

        calls: list[list[str]] = []

        def counting_embed(texts: list[str]) -> list[list[float]]:
            calls.append(list(texts))
            return embed(texts)

        # действие: повторный запуск без изменений корпуса
        second = index_corpus(CLEAN_DIR, counting_embed, store)

        # проверка: ни одного вызова API и ни одного изменения
        assert calls == []
        assert second.errors == []
        assert second.inserted == 0
        assert second.updated == 0
        assert second.deleted == 0
    finally:
        conn.close()
