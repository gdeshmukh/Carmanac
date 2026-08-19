# ADR 0021 — Media provenance and company logos

- Status: Accepted
- Date: 2026-08-19
- Depends on: ADR 0002 (provenance attaches to facts), ADR 0003 (raw records and
  external IDs), ADR 0007 §8 (per-source assertion stores)

## Context

The schema reserved `media_assets` and `media_attachments` before any media was
ingested. It got the licensing requirement right but stopped halfway through the
project's later provenance design: an asset carried a source and raw record,
while the attachment did not. The important statement — “this file is this
company's logo” — therefore had no source, no exact scrape and no same-source
history.

The first real use is a company logo. Wikidata exposes logo image statements as
P154 values pointing to Wikimedia Commons files. Matching those files to
companies by label, tag, filename or slug would reopen an identity problem the
database has already solved: every displayed company already has one or more
Wikidata QIDs in `external_ids`.

P154 is also not guaranteed to be singular. A company may have historical and
current marks, color variants or several live statements. A fetch order is not
a selection rule.

## Decision

1. **The asset and its attachment are separate facts.** `media_assets` records
   one source's observation of a file, its display rendition, technical metadata
   and reuse terms. `media_attachments` records one source's assertion that the
   asset serves a role on exactly one entity. Both carry `source_id`,
   `raw_record_id`, `scraped_at` and `confidence_score`, and both retain
   same-source history through `superseded_by`.
   Deleting an asset is restricted while any attachment fact references it;
   supersession, not cascading deletion, is the normal lifecycle.

2. **The first role is `company_logo`, not `logo`.** It is valid only on the
   company arc. A future model logo will require an explicit `model_logo` role
   and its own constraint; it will not inherit company-logo behavior by accident.

3. **Company correspondence is QID-exact.** The fetch population is the
   Wikidata QIDs already attached through `external_ids` to companies that hold
   models. P154 statements return Commons filenames. No company name, media tag,
   filename or public slug participates in the match. Curated identity-merge
   members still land, but only the canonical QID contributes company facts,
   matching the existing reconciler rule for names, dates and other fields.

4. **The two source claims stay distinct.** Wikidata is the source for the
   company-to-file attachment. Wikimedia Commons is the source for the file,
   rendition URL, dimensions, content hash, licence and attribution. Each gets
   its own raw record. The attachment points to the Wikidata record; the asset
   points to the Commons record.

5. **Only one mechanically current file attaches.** Deprecated statements and
   statements whose qualified time span has ended are ineligible. Preferred
   rank wins over normal rank for the canonical QID. Exactly one licensed
   Commons file attaches; several open a `multi_value` flag on `company_logo`;
   none leave the company without a logo. The pass never chooses by response
   order.

6. **Source-hosted renditions are the first storage mode.** `rendition_url`
   records the Commons thumbnail used for display and `source_url` records its
   description page. `storage_url` remains nullable for a future owned copy.
   File bytes do not enter Postgres. Public deployment may add durable object
   storage without changing the provenance or attachment model.

## Consequences

- Media now follows the same entity/fact and per-source supersession rules as
  the rest of the database before the first media row lands.
- A changed P154 statement retires the prior Wikidata attachment; a corrected
  Commons description retires the prior asset observation. History remains
  queryable from both.
- Missing or ambiguous logos remain visible data gaps. The frontend must not
  manufacture a mark or silently substitute a Wikipedia-local non-free file.
- The homepage can later read live `company_logo` attachments and remain a thin
  company inventory. Its layout is outside this decision.
