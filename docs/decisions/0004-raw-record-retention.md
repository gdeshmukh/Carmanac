# ADR 0004 — Raw record retention tiered by re-fetchability

- Status: Accepted
- Date: 2026-07-24
- Amends: the "raw scrape data is never discarded" invariant in `CLAUDE.md`

## Context

The invariant read, without qualification:

> **Raw scrape data is never discarded.** Separate `raw_scrape` schema holds
> untransformed source records permanently.

The first real ingest showed it is too blunt in one direction and not the point
in another.

**It protected our own bugs.** The Wikidata landing code initially hashed
payloads whose `GROUP_CONCAT` lists were in an unstable order, so the same
entity re-landed as a spurious "change". Those rows are not evidence of anything
Wikidata said — they are evidence of a defect since fixed. Read literally, the
invariant made cleaning up after ourselves a violation.

**It conflated storage with trust.** The instinct to prune data judged
untrustworthy is reasonable but aims at the wrong layer. What a source claimed
is evidence regardless of whether we believe it, and deleting it forfeits three
things worth keeping: measuring a source's quality over time (which needs its
wrong answers retained), re-deriving facts when the matcher improves, and
answering "why does the page say 225 hp?".

**Its real purpose is irreversibility, not permanence.** Wikidata is CC0 and
re-fetchable in about six seconds; its raw records behave like a cache. A Tier 3
forum thread or an archive.org brochure may vanish and never be re-fetchable —
that is the case the invariant exists to protect, and the two do not deserve the
same rule.

Size is not the motivation. Measured: ~1 KB per record, 7.1 MB for 7,226
records. Even at millions of records this is single-digit GB, and `raw_scrape`
is a separate schema precisely so bulk there never pollutes the relational core.

## Decision

Retention is tiered by whether the record can be fetched again, not by age or
by how much we trust it.

**Archival — never deleted.** Tier 3 and Tier 4 sources (marque wikis, forum
threads, club archives, OCR'd brochures). These are ephemeral, often
single-copy, and re-scraping may be impossible. The original invariant applies
here unchanged.

**Cache — may be pruned and re-landed.** Tier 1 and Tier 2 sources with stable
programmatic access (Wikidata, NHTSA vPIC, EPA bulk data). Deleting these costs
only the time to re-fetch. Pruning is permitted when it serves correctness — a
schema change to the landing shape, a fixed extraction bug — and is expected to
be rare.

**Bug artifacts — always deletable, any tier.** Records that reflect a defect in
our own pipeline rather than what a source returned are not evidence and are not
protected.

**Trust never justifies deletion.** A source judged unreliable is demoted in
reconciliation, not erased. The evidence is what justifies the demotion.

Deleting raw records must not orphan facts. `field_provenance`,
`configuration_attributes`, and the association tables carry a *nullable*
`raw_record_id`, so a pruned record leaves the fact and its `source_id` /
`scraped_at` intact — the audit trail survives; only replay is lost.

## Consequences

- `CLAUDE.md`'s invariant is amended to state the tiering. The archival
  guarantee for Tier 3/4 is unchanged.
- **What is lost by pruning is re-derivation, not provenance.**
  `field_provenance` alone still answers "which source claimed this, and when".
  Only replaying an improved matcher without re-scraping requires the payload.
- A future prune of a Tier 1 source should re-land immediately afterwards, so
  the window without raw backing is short.
- Deletion is a deliberate, recorded act, not routine maintenance. Any prune is
  noted in `PROGRESS.md` with its reason.
- Retention is not yet enforced in schema. `sources.tier` already carries the
  information needed to distinguish archival from cache; a guard (trigger or
  application check) preventing deletion of Tier 3/4 records is a sensible
  follow-up once pruning is anything other than manual.
