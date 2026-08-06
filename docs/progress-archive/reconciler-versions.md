# Reconciler version history

`reconciled_records.reconciler_version` stamps which version processed each
raw record, making staleness queryable. This is the version → change mapping;
it lived as a comment block in `carmanac/reconcile/policy.py` until the
2026-08 comment cleanup moved it here.

| v | date | change |
| --- | --- | --- |
| 1 | 2026-07-28 | First companies pass (ADR 0007). |
| 2 | 2026-07-29 | Admission requires affirmative evidence (target class, builder class, or pin); boilerplate-only class sets quarantine. |
| 3 | 2026-07-29 | Plausibility rules at projection open `implausible_value` flags (a single wrong claim has no disagreement for `multi_value` to catch — the AMG "founded 1812" case). |
| 4 | 2026-07-29 | The vPIC match pass (ADR 0008): cross-source identity, corroboration-admission, vPIC-sourced roles. |
| 5 | 2026-07-29 | The match-queue review: company/brand duplicate merges into `IDENTITY_MERGES`, first `VPIC_MATCHES` judgments, `PINNED_DENY`, merge members admit through their canonical entity. |
| 6 | 2026-07-29 | The no-match review batch: 37 more `VPIC_MATCHES` judgments, Consulier brand → Industries merge, Moke International admit pin; vPIC external ids gain the `make:` prefix. |
| 7 | 2026-07-29 | The vPIC models pass (ADR 0010): the first `models` rows, under matched makes only, name asserted through `field_provenance`, slug collisions flagged rather than auto-suffixed. |
| 8 | 2026-07-30 | The duplicate-name sweep's approved batch: 11 same-entity pairs from the 105-group exact-name review merge; namesake and ambiguous groups deliberately stay separate pending cross-source checks. |
| 9 | 2026-07-30 | The Wikidata models sweep pass (ADR 0012): match/enrich only, lines + memberships, direct-case generations; the labeled-set capture lands with it (`match_decisions` log, resolution reasons on every flag close, the negative-match registry). |
| 10 | 2026-07-31 | Name-form evidence ranks (ADR 0013): labels outrank aliases, the cross-badge guard, prefix stripping uses vPIC make names, refreshes preserve the match method in the decision log. |
| 11 | 2026-07-31 | The year pass and the EPA attach (ADR 0014): catalogue periods under models, the first configurations, generation placement NULL and evidence-gated. |
| 12 | 2026-07-31 | EPA powertrain facts, not entities (ADR 0015): corrected `trany` mapping (AM → NULL, AV → cvt), cng/hydrogen fuels, aspiration/flex-fuel EAV, the sole-source column refresh. |
