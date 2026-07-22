# ADR 0003 — Raw landing zone and external ID mapping

- Status: Accepted
- Date: 2026-07-22

## Context

Two review findings, both additive, both required before the first scraper runs.

**Raw records had nowhere to land.** The `raw_scrape` schema existed but was
empty, and no fact-bearing row could point at the specific fetch it came from.
`source_id` says "Wikidata"; it does not say *which* request, on what date,
returning what payload. The architecture invariant "raw scrape data is never
discarded ... for re-reconciliation when matching logic improves" is
unenforceable without that link — re-reconciliation means re-deriving facts
from raw records, which needs the join to exist.

**`wikidata_qid` as a column does not generalize.** Seven tables carried a
`wikidata_qid` column. NHTSA vehicle IDs, EPA `vehicle.id`, and marque-wiki
slugs have nowhere to go, so each new source would need a schema migration, and
there is no place for a source to supply two candidate IDs.

## Decision

**`raw_scrape.raw_records`** — the permanent landing zone. Columns: `source_id`,
`url`, `external_id`, `fetched_at`, `http_status`, `content_hash`, and a
`payload` (JSONB, suitable for the Tier 1 JSON/CSV sources). A nullable
`storage_url` is included for the future case where bulk Tier 3/4 HTML lives in
object storage (S3/R2) with only a pointer here — that keeps the invariant
("raw data is never discarded") without bloating Postgres, and is left as a
follow-up decision rather than committed now. `content_hash` makes re-scraping
idempotent: an unchanged payload need not be re-parsed.

Fact-bearing rows gain a nullable `raw_record_id` FK
(`field_provenance`, `configuration_attributes`, `configuration_engines`,
`configuration_transmissions`), so any fact traces to the exact scrape that
produced it.

**`external_ids`** — one table mapping `(source_id, external_id)` to an entity,
using the same exclusive-arc shape as `field_provenance` (one nullable FK per
entity type, `CHECK` exactly one set) to keep referential integrity.
`UNIQUE (source_id, external_id)` enforces that one source's identifier maps to
one entity. The per-table `wikidata_qid` columns are **dropped**; Wikidata QIDs
become rows here with the Wikidata source. This preserves the "Wikidata QID is
the universal join key" invariant in intent — the QID is still the join key —
while storing it in a form that extends to every other source.

## Consequences

- The `wikidata_qid` unique columns are gone; a Wikidata QID lookup becomes a
  join to `external_ids` filtered by the Wikidata source. `CLAUDE.md`'s wording
  ("universal join key ... wherever a vehicle entity has one") stays true; only
  the storage changes.
- `payload` as JSONB is a Tier 1 decision. The object-storage path for bulk
  HTML is deliberately deferred to its own ADR, but the `storage_url` column
  means adopting it later needs no schema change to `raw_records`.
- Every fact can now answer "where exactly did this come from," which is the
  precondition for a re-reconciliation pipeline.
