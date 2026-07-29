"""Shared landing-zone plumbing: source lookup, hashing, and the raw upsert.

Extracted from the Wikidata landing module when vPIC arrived (the second
source is when sharing stops being speculation). Every source lands the same
way: canonical payload -> content hash -> `ON CONFLICT DO UPDATE` bumping
`last_seen_at`. The per-source modules own only the fetch and the payload
shape; the idempotency/revert semantics (foundation review F3) live here,
once.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from carmanac.db.models import RawRecord, Source

# Postgres caps a statement at 65535 bind parameters. At ~7 columns per row a
# single multi-thousand-row INSERT would sit uncomfortably close to that
# ceiling, so batches are chunked well below it.
_CHUNK_SIZE = 500


@dataclass(frozen=True)
class LandResult:
    """What one landing run did. `fetched - inserted` is the unchanged count."""

    fetched: int
    inserted: int

    @property
    def unchanged(self) -> int:
        return self.fetched - self.inserted


def content_hash(payload: dict[str, Any]) -> str:
    """Stable SHA-256 of a payload.

    `sort_keys` and the compact separators make the hash depend on the
    *content* only - not on key ordering, which no source promises to keep
    stable between runs. Without that, an unchanged record could hash
    differently on every fetch and re-land forever.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_source(session: Session, name: str) -> Source:
    """Fetch the `sources` row by name.

    Deliberately does not create it: sources are reference data describing
    what a source *is* and at which tier it sits. An ingest silently inventing
    one would let a typo produce a second, untiered "Wikdata" that facts then
    hang off. Missing is an error worth failing on.
    """
    source = session.scalar(select(Source).where(Source.name == name))
    if source is None:
        raise LookupError(
            f"No `sources` row named {name!r}. Reference data must exist before "
            f"ingestion; seed it first."
        )
    return source


def upsert_raw_records(session: Session, rows: list[dict[str, Any]]) -> int:
    """Land rows into `raw_scrape.raw_records`; returns how many were new.

    DO UPDATE, not DO NOTHING (foundation review F3): an unchanged payload
    must still bump `last_seen_at` - it is what makes "the current record per
    (source, external_id)" answerable, and the only thing that makes an A-B-A
    revert representable. Constraint-driven and race-safe under concurrent
    ingests, unlike a read-then-write check.
    """
    # Two entities can share a hash only if their payloads are byte-identical,
    # which for distinct external ids cannot happen - but a single response
    # *can* repeat a row, and ON CONFLICT rejects touching one row twice in
    # one statement. Deduplicate in-batch.
    seen: set[str] = set()
    unique_rows: list[dict[str, Any]] = []
    for row in rows:
        if row["content_hash"] not in seen:
            seen.add(row["content_hash"])
            unique_rows.append(row)

    inserted = 0
    for start in range(0, len(unique_rows), _CHUNK_SIZE):
        chunk = unique_rows[start : start + _CHUNK_SIZE]
        stmt = (
            pg_insert(RawRecord)
            .values(chunk)
            .on_conflict_do_update(
                index_elements=["source_id", "content_hash"],
                set_={"last_seen_at": func.now()},
            )
            # DO UPDATE returns every row (hit or new), so distinguish real
            # inserts via xmax: 0 means the row was created by this statement.
            .returning(RawRecord.id, literal_column("(xmax = 0)").label("was_inserted"))
        )
        inserted += sum(1 for row in session.execute(stmt) if row.was_inserted)
    return inserted
