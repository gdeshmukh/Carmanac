-- Runs ONCE, automatically, the first time the database volume is created.
-- (Postgres runs every *.sql in /docker-entrypoint-initdb.d on first init.)
--
-- Purpose: enable the extensions our schema depends on, BEFORE any Alembic
-- migration tries to create a column of type `vector` or a trigram index.
-- This is the only place schema-adjacent SQL lives outside Alembic, and it's
-- here only because an extension must exist before migrations can reference it.
--
-- Everything else — tables, columns, indexes — is owned by Alembic. Do not add
-- CREATE TABLE statements here.

CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector: semantic search (later phase)
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- trigram fuzzy matching on names/slugs

-- The raw_scrape schema (untransformed source records, kept permanently).
-- Created here so it exists from day one; its tables come later via Alembic.
CREATE SCHEMA IF NOT EXISTS raw_scrape;
