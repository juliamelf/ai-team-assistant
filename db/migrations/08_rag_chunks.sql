CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS rag;

CREATE TABLE IF NOT EXISTS rag.chunks (
    chunk_id            text PRIMARY KEY,
    doc_id              text NOT NULL,
    ps_code             text NOT NULL,
    doc_type            text NOT NULL,
    doc_role            text NOT NULL CHECK (doc_role IN ('source', 'distractor')),
    doc_version         text NOT NULL,
    chunk_type          text NOT NULL CHECK (chunk_type IN ('doc', 'otf', 'tf')),
    otf_code            text,
    tf_code             text,
    qualification_level smallint,
    heading             text NOT NULL,
    text                text NOT NULL,
    content_hash        text NOT NULL,
    embedding_model     text NOT NULL,
    embedding           vector(2048) NOT NULL,
    indexed_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chunks_doc_id_idx ON rag.chunks (doc_id);
CREATE INDEX IF NOT EXISTS chunks_tf_code_idx ON rag.chunks (tf_code);

INSERT INTO app.schema_migrations (version, note)
VALUES ('08', 'rag.chunks — фрагменты корпуса профстандартов с эмбеддингами')
ON CONFLICT (version) DO NOTHING;
