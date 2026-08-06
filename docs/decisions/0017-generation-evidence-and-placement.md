# ADR 0017 — Generation evidence: Wikipedia time, dated-overlap placement, and where existence comes from

- Status: §1–3 Accepted and implemented (2026-08-06, same branch as
  ADR 0016 — the machinery ran live and its first run's two lessons are
  folded in as amendments); **§4 approved in principle**
  (review, 2026-08-06 — the ADR 0012 "approved in principle,
  deliberately not in v1" pattern): the direction stands, and its own
  implementation branch proves it out — probe-first on the full-article
  fetch and the section parse, with the keying/dedup/naming questions
  settled against real pages before any generation is minted.
- Date: 2026-08-06
- Depends on: ADR 0016 (company-anchored generations,
  `generation_model_links` as the candidate gate), ADR 0014 (placement
  is configuration-level and evidence-gated), ADR 0013 (evidence ranks),
  ADR 0012 (whose §existence rule §4 proposes to revise), ADR 0003/0004
  (raw landing and retention)

## Context

Every generation fact ultimately answers two questions: **does this
generation exist** (identity), and **when was it** (time). The sources
answer them very unevenly, measured live (2026-08-06):

- **Wikidata cannot supply generation time.** 936 of 14,524 model-sweep
  records carry any start date; 305 an end date; even the BMW E30 has
  none. Of the 151 generations it minted, 4 had spans.
- **Wikidata's generation *existence* is a thin mirror of Wikipedia's.**
  Its generation entities exist mostly where a per-generation Wikipedia
  article exists (they are sitelink shadows); where Wikipedia keeps
  generations as sections of the nameplate page, Wikidata usually has
  nothing — the Mercedes-AMG GT has zero generation entities despite a
  fully sectioned article (`#First_generation_(C190/R190)`).
- **Wikipedia holds the actual curation.** Per-generation articles carry
  infoboxes with `production` AND `model_years` (the US axis vPIC/EPA
  periods live on); nameplate articles carry per-generation *sections*
  whose headings follow a cross-marque convention — ordinal + chassis
  code + year, `First generation (XW10; 1997)` — each with its own
  infobox, often with deliberately maintained anchor spans
  (`<span class="anchor" id="S13">`).
- **The coverage funnel says existence is the choke point**: 1,114
  models carry configurations; 432 of them have a 1:1-matched QID with
  an enwiki nameplate article; only **51** have any linked generation.
  A 36-article sample of the 381 articled-but-generation-less models
  found ~1/3 with per-generation section infoboxes — extrapolating to
  roughly 125 more models whose generations are mineable today.
- autoevolution.com was evaluated as a segmentation source and is
  **parked**: robots.txt walls off AI/data crawlers wholesale and its
  terms page refuses programmatic fetches; a human read of the terms
  found them social-layer only, so the route back is a
  permission ask, not a differently-named crawler. It also has no QID
  join, so ingesting it would open the name-matching surface the
  Tier-2/3 gate guards. Manual reference use in flag review stays
  legitimate.

## Decision

### 1. Wikipedia enters through inherited identity, never through matching

Wikipedia is Tier 2, and the charter gates Tier 2/3 sources on measured
matcher precision. That gate is about *name matching* — and this
ingestion does none. Every article is reached through the sitelink
recorded on a QID that is already 1:1-attached to one of our rows;
identity is inherited, not inferred. Stated here so the first Tier 2
ingestion is a flagged, explicit non-violation.

The lander (`carmanac/ingest/wikipedia/`) fetches **section-0 wikitext**
per article (MediaWiki API, polite rate, honest UA) and lands it
untransformed as `infobox:<QID>` records — kinds readable from the
namespaced id, parsing kept in the pass, raw wikitext retained as the
Tier 2 archival record (a revision is not re-fetchable once the page
moves on). v1 scope: the 446 generation-relevant sitelinks (all landed,
zero dead links).

**Tier standing (ruled in review): tier never quarantines facts.**
Review queues exist for identity ambiguity, not source rank. Identity
here is settled by the sitelink, so infobox assertions write directly
through `field_provenance` — no review gate. For generation coding there
is effectively no first-hand source, so Wikipedia is the *primary*
asserter at this level by design.

### 2. The time-and-codes pass

- `generations.start_year` / `end_year` from the infobox **production**
  span (open end = still in production). The **US model-year span** is
  deliberately not stored — no column for a US-specific reading — and is
  parsed from raw at placement time.
- `generations.chassis_codes` from the **article title parenthetical**
  through the same strict extractor the label pass uses (`BMW 3 Series
  (E30)` → E30); prose parentheticals assert nothing.
- **Precedence, with mechanical teeth**: the generation facts Wikidata
  supplied were parsed out of *labels* — extraction from a naming
  string, not a deliberate claim — and tier-first arbitration would let
  them beat an infobox. Ruled: for generation-level fields, infobox
  assertions supersede label-derived ones. The wd-models pass keeps its
  assertions current in `field_provenance` but skips projecting fields
  the infobox pass asserts (`assert_field_facts(skip_projection=…)`).
  Field-scoped, provenance-visible, revisited when the
  confidence-methodology ADR lands.
- A span that does not reduce to exactly one range **flags
  (`implausible_value`) and asserts nothing** — multi-plant and
  per-body date lists are the reviewer's question, never a hull guess.
  Parser tuned against the real corpus: 280/375 production spans parse;
  74 multi-range fields flag.
- **Redirected articles assert nothing** (first live run's lesson): the
  "Honda Civic Hybrid" sitelink resolves to the whole-nameplate Civic
  page, whose 1972–present span must not land on one generation. A
  resolved title differing from the requested one beyond case/underscore
  wobble is a subject change; the record waits and earlier assertions
  tombstone back to NULL.

### 3. The placement pass: unique dated overlap

Candidates for a configuration = generations linked to its model
(`generation_model_links`) whose placement span contains the
configuration's catalogue period:

- The infobox `model_years` span wins **exact** when the period is
  `model_year`-kind and the field is present. Otherwise the production
  span with **end+1 slack** — a US model year routinely outruns
  production by one calendar year; the start gets no slack (a car cannot
  be catalogued before it is built). Slack applies *before* the
  uniqueness test, so it can only add flags, never force placements.
  Accepted as the general rule; exceptions get dedicated decision
  passes, not a looser rule.
- **Uniqueness counts only when every linked generation is dated**
  (first live run's lesson): one dated match beside undated siblings
  placed a 1991 Celica convertible into `celica-gt-four` because the
  GT-Four page was the only dated candidate. Undated competitors hold
  the configuration in `waits_undated_competitor`, resolving
  mechanically as spans land.
- Exactly one candidate → place, with field-level provenance to the raw
  record whose span decided it. Two or more → NULL + one
  `generation_overlap` flag carrying the candidates. Zero → the normal
  waiting state, logged, unflagged.
- **Body evidence is a candidate VETO, not an overlap tiebreaker**
  (ruled in review, 2026-08-06). The C190 and X290 are vastly different
  cars sharing a badge — the 4-Door is not a coupe — and the overlap
  flag was only ever the fallback under year-only evidence, never the
  goal state. Where a generation's infobox asserts body styles and the
  configuration carries a contradicting body signal (trim strings today;
  EPA's ~100%-filled size class in raw, unconsumed, is the stronger
  candidate), the generation is **not a candidate** regardless of year.
  Where candidates differ in body but the configuration carries no body
  signal, placement waits rather than guesses. Follow-up implementation,
  same rule discipline as the rest of §3.
- The pass is the **sole placer** (ADR 0015's sole-source posture): a
  recomputed answer supersedes the old one, including back to NULL, with
  the trail kept.

Live state at acceptance: 197 configurations placed (the Camry's whole
eight-generation run via model_years exact; Legacy's seven via
production+slack), 354 overlap flags, 616 undated-competitor waits,
22,356 waiting on inventory; 650 wrong-or-premature placements from the
pre-amendment run withdrawn with their supersession trail. Re-runs are
exact no-ops.

### 4. PROPOSED: existence comes from Wikipedia too — section-minted generations

The funnel shows the binding constraint is not time but *existence*:
placement can only reach the 51 models Wikidata gave generations. This
section proposes revising ADR 0012's rule that **only P179 membership
creates a generation**:

- For a model whose QID carries an enwiki sitelink (432 today), the
  nameplate article's **per-generation sections** mint generations: the
  heading supplies ordinal, chassis codes, and start year
  (`First generation (XW10; 1997)`); the section's own infobox supplies
  the spans. Identity is still inherited — the article is reached
  through the model's 1:1-matched QID, and sections are structural
  parsing inside that scope, not name matching.
- Wikidata is **demoted from gatekeeper to contributor**: its
  generation entities keep minting where they exist (their QIDs remain
  the best join keys), its P179/chain structure becomes corroborating
  evidence, and Wikipedia sections mint where Wikidata is silent.
- **Conflated filings and link completeness** (the AMG GT ruling):
  vPIC files ONE `AMG GT` whose catalogue holds two genuinely
  different cars — the C190/C192 sports car and the X290 4-Door — an
  artifact of Mercedes' naming, not of the sources: Wikipedia and
  Wikidata keep them fully separate (own articles, own entities;
  Q50368653 is in the sweep). Section-minting the sports car's article
  yields C190/R190 and C192 *only* — it can never mint the X290 into
  the wrong car. But that leaves a trap this section must close: with
  only C190 linked, a 2019 4-door configuration would *uniquely*
  year-match it, and the undated-competitor guard cannot fire against a
  car that is not linked at all. The answer is twofold: the §3 body
  veto (a 4-door is never a C190 candidate), and representing the
  sibling honestly — the X290 minted as its **own** company-anchored
  generation from its **own** article, linked to the as-filed model
  because the link means "this filing's catalogue contains this
  generation's cars" (true of vPIC/EPA). That link is never fabricated
  by section parsing; it arrives from the sibling's own article plus a
  curated judgment. End state: coupes place into C190, 4-doors into
  X290, and `generation_overlap` fires only for rows carrying no body
  signal.
- Open questions the review must settle before implementation:
  - **Keying**: section headings rename; anchor spans (`id="S13"`) are
    deliberately maintained but not universal. Candidate key: the
    nameplate QID + normalized ordinal (`Q18011551#1`), with codes and
    heading text as facts rather than identity. Ordinal-only headings
    ("first/second/third", no codes) are the minority but real and must
    key cleanly.
  - **Dedup**: when Wikidata later grows a C190 entity, the
    section-born generation must adopt its QID (sitelink+section
    correspondence) rather than duplicate.
  - **Naming/slugs**: section-born generations need a display-name rule
    (`AMG GT (C190)` vs `AMG GT first generation`) consistent with the
    company-scoped slug uniqueness.
  - **Fetch shape**: full-article wikitext for the 432 nameplate pages
    (a second polite sweep, ~430 requests), landed as a distinct record
    kind beside the section-0 `infobox:` records.

## Consequences

- Time and placement machinery is live and self-correcting; its
  coverage ceiling is generation inventory, which §4 is designed to
  raise (~125 more models' generations mineable at current sampling,
  against 51 covered today).
- The proof car stays honestly NULL until §4 lands: the AMG GT has no
  generation rows to place into. With §4 plus the body veto, its coupes
  place into C190 and the 4-doors into X290 — no overlap flag, because
  they were never each other's candidates. The flag remains only for
  rows with no body signal.
- Wikipedia's demotion of Wikidata here is evidence-driven, recorded,
  and reversible per field through provenance — no raw data is
  discarded either way (ADR 0004).
