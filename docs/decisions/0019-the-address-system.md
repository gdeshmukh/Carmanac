# ADR 0019 — The address system: slug history, disambiguators, and canonical routes

- Status: Proposed (2026-08-10; implementation rides this branch per the
  one-branch lifecycle — acceptance at PR review)
- Date: 2026-08-10
- Depends on: ADR 0016 (company-anchored generations), ADR 0017 §4 (section
  minting and its naming rule, amended here), ADR 0013 (name-form evidence and
  prefix stripping), ADR 0011 §4 (identity is ours; external ids are 1:1
  correspondence), ADR 0010 §2.3 (slug collisions flag, never auto-suffix),
  ADR 0007 §7 (slugify; the QID-suffix collision rule, replaced here),
  ADR 0004 (retention; our own artifacts are deletable), ADR 0002 (provenance
  attaches to facts, not identity)

## Context

The read surface (PROGRESS Next: the first entity pages) is queued directly
behind this ADR, and the 2026-08-10 three-lens review established that slugs
are not display-only. Verified live before drafting:

- **Slugs are already load-bearing machine identifiers.** Three curated
  registries key recorded human judgments on `"company-slug/model-slug"`
  pairs (`WIKIDATA_MODEL_MATCHES`, `WIKIDATA_MODEL_NEGATIVES`,
  `SECTION_ARTICLE_MODELS` — the AMG GT routing that placed the proof car).
  A rename today silently disarms them: a stale negative does not merely stop
  applying, it **re-arms the exact match a human dismissed** (the rung-3
  filter checks the current pair and skips nothing on a miss); a stale
  routing key stops the founding existence mechanism with one fresh-clone-
  calibrated `log.warning`.
- **140 companies carry source-leaking `-q<digits>` slugs** — the class the
  2026-07-29 ruling condemned ("`tesla-q124981765` leaks Wikidata's
  identifier scheme into public identity"). All 140 verified: every suffix is
  a Wikidata QID. The real Tesla is among them, and 179 configurations embed
  the wart as their slug prefix. Confined to companies: the model and
  generation regex hits (Audi Q5, Infiniti Q60, Kia's NQ5) are all real names.
- **No alias or slug-history mechanism exists anywhere.** Every slug column
  is single-valued and NOT NULL; a rename loses its old address irreversibly.
  That is why this ADR precedes even the Tesla fix: executing the rename
  first would destroy the very history the fix should record.
- **Generation slug grammar has drifted** because display names feed
  `slugify` and two mint sites compose display names differently:
  `mx-5-na` beside `2000-2007-subaru-impreza` (a section heading slugged
  whole) and `category-honda-hr-v-1st-generation` (a leaked `Category:`
  title). Generation slugs are company-scoped (`uq_generations_company_id_slug`)
  while the charter nests generation routes under models; every generation
  currently links exactly one model, so today's URL unambiguity is a
  coincidence the line-membership turn will end.
- **Configuration slugs are clean but composed**: globally unique, all
  23,523 prefixed by their company and model slugs frozen at mint time.
  Composition also omits two natural-key columns (market, body style) —
  invisible today only because every row is US-market with NULL body style.
  The collision suffix is an insertion-order counter, and its single live
  use (`…-e350-4matic-4wd-2`) papers over a casefold duplicate rather than
  disambiguating two cars.

The design was drawn from three independent proposals (migration-safety,
machine-consumer, librarian lenses) adversarially reviewed against the full
constraint set; the rulings below are the synthesis. One sentence of scope:
this ADR ships **data and guarantees**. Serving — 301s, disambiguation
pages, any route handler — ships with the read surface.

## Decision 1 — Slug history: `slug_aliases`, trigger-enforced

**No rename may ever discard its old address.** The mechanism is a table plus
database triggers, because only the database catches every writer — passes,
decision scripts, and a raw psql session alike. "Mechanically impossible" is
the bar, and convention cannot meet it.

### The table

`slug_aliases` — one row per retired address:

- `entity_kind` (CHECK: `company`, `model`, `model_line`, `generation`,
  `configuration`, `engine`, `transmission`), `slug` (the retired address),
  `scope_company_id` (NOT NULL exactly for the per-company kinds — model,
  model_line, generation — mirroring the live uniqueness scopes; CHECK
  enforces the correspondence), an exclusive-arc target (one FK column per
  kind, exactly one non-NULL, arc kind must match `entity_kind`), `reason`
  (CHECK: `rename`, `merge`), `created_at`.
- `UNIQUE NULLS NOT DISTINCT (entity_kind, scope_company_id, slug)` — one
  promise per address.
- Every FK indexed (working rule); arc and scope FKs are `ON DELETE
  RESTRICT` — a row that history points at cannot be deleted until a script
  re-points the history, which is exactly the merge discipline below.

**Classification (ADR 0002).** `slug_aliases` is reconciler bookkeeping, the
`reconciled_records` / `match_decisions` species — no source ever asserted an
alias, so it carries no provenance columns; the charter's fact-table rule is
satisfied by classification, not omission. It is a third species within that
family: **durable adopted state**, like `raw_scrape.raw_records` — not
reproducible by re-running passes. It survives any derived-state reset by
the same rule as raw data, travels with the database, and is legitimately
empty in a from-scratch environment (which has served nothing and so has
nothing to honor). No entry ever goes in `external_ids` — aliases are
addresses, not correspondence (ADR 0011 §4 untouched).

### The trigger contract

One trigger function family, installed by the migration (the
`a1c4e7b93f20` updated-at precedent: database-level because ingestion does
not always go through the ORM), all guarded by `IS DISTINCT FROM` so no-op
updates write nothing:

- **BEFORE UPDATE OF `slug`** on all seven slug-bearing tables — and of
  `company_id` on the three per-company tables, because a re-parent changes
  the address even when the slug text does not (alias scope is always
  `OLD.company_id`, where the address actually lived):
  1. The new address equals an alias bound to a **different** entity →
     RAISE. No entity may take over another's history.
  2. The new address equals an alias bound to **this** entity → delete that
     alias. A promise the entity itself re-fulfills is discharged, not
     broken; renaming back (`a → b → a`) is legal.
  3. Insert the alias row for the old address (`reason = 'rename'`,
     `ON CONFLICT` re-pointing an existing same-address alias at this row).
- **BEFORE INSERT** on all seven tables: a new row's slug equals any alias
  in its scope → RAISE. This is what makes the mint sites' in-memory
  collision caches *safe* rather than merely polite: a rename committed
  mid-pass-run cannot have its freed address silently re-minted by a stale
  cache — the worst case is a loud aborted run, never a theft.
- **BEFORE DELETE** on all seven tables: RAISE unless an alias for the
  row's own address already exists (pointing at its successor), or the
  session has set `carmanac.allow_address_drop` — the explicit, greppable
  escape reserved for ADR 0004's our-own-bug artifacts. Deleting a row that
  other aliases still target is blocked by the arc FKs themselves.

**Who writes what.** A rename writes nothing by hand — the trigger emits the
alias in the same statement, so a batch cannot half-apply. A **merge** script
must, in one transaction: alias the loser's own slug to the survivor
(`reason = 'merge'`), re-point any aliases targeting the loser at the
survivor, re-scope the loser's per-company alias rows to the survivor
(scope follows the merge-successor; the company's own alias chain preserves
what the old scope meant), then delete. Every step is forced by a RESTRICT
or the delete guard — the `merge_duplicate_companies.py` precedent of
addresses vanishing with the row cannot recur. A re-scope that collides with
the survivor's existing history halts the merge for a human ruling.
**Passes never rename** — slugs stay write-once for every pass, and no pass
acquires any rename authority from this ADR.

**Address writers serialize.** Every pass run and every rename/merge script
takes one shared Postgres advisory lock for its transaction. Passes are
manually conducted and never concurrent in practice; the lock makes the
practice a property, and closes the one hole the triggers cannot see (a
configuration minted mid-run composing a just-retired company prefix into a
brand-new string).

**Mint sites respect history.** Every collision cache loads live slugs ∪
alias slugs for its kind and scope; a mint landing on an alias-held address
follows the kind's collision policy (flag) in the normal case, with the
INSERT trigger as the mechanical backstop. `model_lines` — the one kind
whose slug is its row-lookup natural key — additionally resolves lookup
misses through the alias table to the *same row*, so a line rename can no
longer cause a duplicate mint.

**Alias rows are append-only**, with exactly two sanctioned exceptions: the
trigger-discharged self-alias (rule 2 above), and a window-open prune — the
gated script that created an alias for a never-served address may delete it
in the same review cycle, under ADR 0004's own-artifacts clause. Once the
read surface ships, aliases are permanent. A third, narrow operation exists
by ruling but ships no machinery now: a dry-run-gated script may **re-point**
an alias when a review rules that an address's meaning moved (the ordinal
case: a mid-history discovery renumbers `<nameplate>-third-generation`, and
the vacated ordinal address rightfully belongs to the newly minted true
third generation). Retargeting is a recorded judgment like any other.

**Addresses retired before this ADR are out of warranty.** The Santana, JLR
and Bugatti merges and the ADR 0013 re-slugs discarded addresses when
nothing had ever been served; their history lives in PROGRESS and the ADRs
as prose. No backfill.

## Decision 2 — Company collisions: flag-never-suffix, curated pins, nobody-bare clusters

**The QID suffix dies** (replacing ADR 0007 §7's collision rule — the
flagged revision that executes the 2026-07-29 source-neutrality ruling), and
with it the last auto-suffix on an identity kind. The collision policy is
now uniform across companies, models, lines and generations: **flag, never
suffix**.

- **At mint time**: a company whose slug collides with a live or aliased or
  reserved slug mints nothing and opens a `match_review` flag
  (`namesake_collision`) — the quarantine posture of ADR 0007, one column
  over. Stated consequence, accepted deliberately: an unminted company is
  invisible to every downstream pass until a human pins it, exactly as
  models under unmatched makes wait today. Namesake clusters are
  overwhelmingly dead obscure marques; a prominent arrival gets pinned
  within a session.
- **Resolution is a pin**: `COMPANY_SLUG_OVERRIDES: dict[QID, slug]` in
  `policy.py` — each entry a recorded human judgment, code-reviewed,
  rebuild-deterministic, and immune to fact drift (a computed disambiguator
  would rename every time arbitration improved a country or founding year;
  a pin freezes the judgment). The grammar *guides* the pin — encyclopedia-
  style qualifiers from what the entity is: place, era, or product
  (`meteor-detroit`, `standard-coventry`) — but the pin is the decision.
  The same registry cures the non-ASCII case: `slugify`'s external-id
  fallback (the same source leak wearing another hat) is replaced by
  flag-and-wait for a curated romanization.
- **Bare-slug arbitration**: in a contested cluster, **nobody holds the
  bare slug**. That is the only rule that is provably ingestion-order-free —
  the current bare holders are simply the first arrivals, QID-ordered,
  which is the source-order dependence the ruling condemns. Vacated bares
  join `RESERVED_COMPANY_SLUGS` (a policy frozenset: code, so occupation is
  rebuild-stable and mechanical) and their trigger-emitted aliases are
  pruned in the same reviewed transaction — the window-open prune above;
  retaining them would fossilize the arrival accident as a permanent
  binding. The reserved bare later serves a disambiguation page.
- **Grandfathering, stated honestly**: when a *new* namesake of a clean
  incumbent lands later, the newcomer flags and waits; the incumbent keeps
  its bare slug. Stability outranks retroactive symmetry once addresses
  have consumers, and no pass ever moves a live address — every rename,
  including an incumbent's, goes through the standing dry-run gate. The
  order-independence guarantee is therefore complete for the batch executed
  now and deliberately partial for future arrivals; that trade is this
  ADR's to own.
- **Tesla**: the only company named Tesla in the database; unique base, no
  cluster, no pin needed. Its slug becomes bare `tesla` by grammar — the
  same answer vPIC-first ingestion would have given, which is the ruled
  test. Its external ids (Q478214, Q124981765, `make:441`) are identity and
  do not move. The suffix batch preflights every unique-base drop against
  the duplicate-name census: a candidate whose name approximates another
  live company joins the hold-list instead (the `AUDI` twin
  `audi-q136087723` is held for the parked merge ruling — merges outrank
  renames).
- `slugify` is hardened once, for every kind: NFKD fold as today, the dash
  family (en/em dashes, the `renaultnissanmitsubishi-alliance` wart) maps
  to hyphens, runs collapse, edges trim.

## Decision 3 — Registry keys: atomic migration, validated loud, no silent path

Registry keys **stay slug pairs** — they are reviewed code, and the pair's
readability is its point. What changes is that staleness becomes impossible
to miss:

- **Validation at pass init, against live pairs only.** The wd-models pass
  and the sections pass each verify, at load, that every
  `WIKIDATA_MODEL_MATCHES` value, every `WIKIDATA_MODEL_NEGATIVES` pair,
  and every `SECTION_ARTICLE_MODELS` value resolves to a live
  (company, model) pair. Any miss while the models table is populated →
  **RAISE**; the run aborts listing the offending entries. An aborted run
  is maximally loud and perfectly recoverable (edit policy.py, re-run) —
  the system's own polarity rule. The negatives thereby fail **closed**: a
  run never proceeds with a disarmed guard. Empty models table → warn and
  continue (the fresh-clone case the sections pass's current comment
  calibrates for). Validation is deliberately not alias-aware: resolving
  keys through history would let production and a fresh environment
  diverge on the same registry text, and would let a merge silently carry
  a negative judgment onto a row its author never judged. Stale text has
  exactly one cure — rewrite the entry.
- **Renames migrate keys atomically, enforced mechanically.** The rename
  scripts preflight every registry entry against the post-rename world and
  refuse to execute while any current `policy.py` entry would go stale —
  the script imports policy at runtime, so the check is real. The same
  commit that renames rewrites the pairs; the init validation is the
  enforcement that this actually happened.
- **Companions**: `EPA_MAKE_MATCHES` gains a load-time warning where it
  silently dropped unresolvable entries (the row-level `unbridged_make`
  flag already catches the symptom; this makes the entry diagnosable). The
  sections pass's redirect-tombstone check moves ahead of the routing
  check, closing the ordering gap where a stale-routed *and* redirected
  article never tombstoned. `scripts/status.py` gains a registry-health
  section: every slug pair in the three registries resolved against live
  pairs, every `NOT_A_GENERATION` / `IDENTITY_MERGES` QID against
  `external_ids`, live ∩ alias emptiness per scope, and reserved slugs
  neither live nor aliased — the always-on check that runs where the data
  exists, which CI structurally cannot.

## Decision 4 — The generation address

**Canonical: `/makes/<company-slug>/generations/<generation-slug>`.** This
is a flagged revision of the charter route map. The entity is
company-anchored with company-scoped uniqueness (ADR 0016); model coverage
is derived, mutable, and legitimately empty (four live rows hold no link) —
so only a company-scoped canonical is a pure function of identity. A
model-nested canonical would need a "canonical model" chosen from link and
placement data that moves on every re-run, and would leave zero-link rows
unaddressable. The literal `generations` segment makes the kind readable
from the address itself.

The model-nested form `/makes/<company>/<model>/<generation-slug>` remains
a **valid non-canonical form for every live-linked model** — guessable,
shareable, printable. What it serves (301 versus page-with-canonical-link)
is the read surface's decision; what this ADR rules is its durability
class: **contextual forms are not durable** — links are evidence and retire
(ADR 0018 retired four), so a consumer keeps the canonical, which never
depends on coverage. Per-model collision of nested forms is structurally
impossible while links stay same-company (company-scoped uniqueness
subsumes every nested rendering); a future cross-company link (rebadges)
that collides **flags, never suffixes**.

**One display-name rule for the kind** (amending ADR 0017 §4's naming rule,
flagged): `<stripped nameplate> (<codes, '/'-joined>)` when chassis codes
are known, else `<stripped nameplate> (<ordinal-word> generation)` —
stripping per ADR 0013 §1 (every recorded company name, including vPIC make
names), applied at **both** mint sites; ordinal words, never numerals; year
ranges never appear in names or slugs. The slug is `slugify(display)` with
**code-dedupe**: when the stripped source label already is the code token,
nothing is appended — Wikidata-born `991` stays `991`, `mx-5-na` stays,
`amg-gt-c190-r190` stays. One rule, two lawful realizations; the variance
that remains follows source naming, not mint-site accident — and under a
company-scoped canonical, `x3-e83` is not stutter but self-description
within BMW's namespace.

**The drift ends mechanically, not by batch alone**: both mint sites gain a
conformance guard — a computed slug that is year-range-prefixed, derived
from a `Category:` title, or embeds the company name **flags instead of
minting** (the sections pass's all-or-nothing rule extends to it). The
rename batch is then a **recompute diff**, not a hand-picked list: every
row where the stored slug fails conformance renames to its grammar form
(the two leaked source artifacts, the two numeral ordinals, the
company-name-embedding class, the `de-ville-1961-64` species — the dry-run
enumerates exactly). The bare-code and name+code majorities stay. The four
demoted rows (ADR 0018) are skipped: they await re-kinding, and forcing
generation grammar onto rows ruled not-generations is churn.

**Codes arriving later do not move addresses.** When the adoption pass
lands chassis codes on an ordinal-named generation, the name fact updates
and the slug stays — passes never rename. A curated batch may promote it
later behind the gate, with the ordinal address carried by alias; an
ordinal renumbering is the alias-retarget case Decision 1 reserves.
Placement (`configurations.generation_id`) and identity keying
(`section:<QID>#<ordinal>`) are FKs and external ids — no rename touches
either.

## Decision 5 — The leaf: `/cars/<slug>`, composed and complete

**The leaf route segment is `/cars/`**, and the compare parameter becomes
`?cars=` in the same pre-publication window — flagged charter edits filling
the blank ADR 0001 left open. The mission's own noun is the car; there is
no reason for the public address to speak schema. **Slug-only**: the
charter's `<configuration-slug-or-id>` tightens to the slug — a composed
leaf slug is never purely numeric, ids never appear in public routes, and
the 71 numeric model slugs are harmless because every route is slug-only.

**Composed slugs are ratified** — they are what makes 23,523 addresses
flat-resolvable, guessable, and readable at the focal page — and the
grammar becomes a **complete** function of the natural key:
`company · model · year · [trim] · [body] · [drivetrain] · [market unless US]`,
every token through the shared `slugify` (retiring the EPA pass's private
casefold grammar, whose per-token stripping minted the 38 double-hyphen
malformations). Body and market tokens appear exactly when the fact is
present and non-US; **the US omission is a frozen grammar constant**, so
the entire current fleet is already conformant — no fleet re-slug, and the
day a Euro-market or body-split sibling lands it mints a distinct address
by fact, not by counter.

**The counter dies.** A collision under the complete grammar means
slugification collapsed two natural keys — almost certainly an
unreconciled duplicate, which is what the census proved the single live
`-2` to be (trim `E350 4Matic` beside `E350 4matic`). Collisions **flag
and mint nothing**, joining every other kind. The live `-2` row stays as
is (its recompute would collide; the dry-run lists it); whether the
natural key should casefold trim is an entity-resolution question recorded
in PROGRESS Open Questions, not smuggled into an address ADR. Honest
consequence: a from-scratch rebuild flags that pair and mints one row —
under flag-never-guess, the flag is the correct outcome.

**Snapshot semantics are the standing rule** (T10): a parent rename never
mechanically rewrites descendant slugs — children resolve by FK, and a
leaf slug is an address, not a live claim. **This branch, window open, one
enumerated exception**: the company batch runs a leaf recompute-diff after
its renames, which sweeps the 179 `tesla-q124981765-*` children, the 38
malformed slugs, and any hardening stragglers into one reviewed, aliased
rename list. Shipping a source leak inside 179 permanent addresses with
zero consumers would be malpractice; after the window closes, any such
sweep is a per-event ruling.

## Decision 6 — Evolvability: the frame, not the grammars

An address is **(kind, scope, slug)**. A new entity kind joins by
registration, never by migration of meaning:

1. one `entity_kind` value and one arc column in `slug_aliases` (a shape
   migration; no existing row changes meaning),
2. a declared scope (global or per-company),
3. a grammar function through the shared `slugify`,
4. a collision policy from the two-policy menu — identity kinds flag and
   cure by curated pin; composed fact-derived kinds flag on residual
   collision,
5. a reserved route segment.

Under `/makes/<company>/`, **models own the bare second segment,
exclusively**. Every other kind lives under a reserved literal segment —
`/generations/`, `/lines/`, `/codes/` — held in a
`RESERVED_ROUTE_SEGMENTS` frozenset that model and line minting check with
their collision caches (zero live conflicts today, verified). The
Lamborghini `huracan` model/line clash resolves by that precedence: the
model keeps `/makes/lamborghini/huracan`, the line serves at
`/makes/lamborghini/lines/huracan`.

**View addresses are queries, not rows** (charter: aggregation pages are
queries over the spine): chassis-code pages live at
`/makes/<company>/codes/<code>`, derive from the `chassis_codes` fact,
mint nothing, and carry no alias history — their stability is the fact's.
A view that ever needs durable history has become an entity and must
register as a kind.

Engines and transmissions get triggers and arc columns now (zero rows,
near-zero cost, and the frame guarantee — whatever their minting ADR
decides inherits aliases and occupancy from day one); their grammar and
collision policy belong to that ADR. Platforms and trim lines join the
same way; the demoted four keep their generation-kind addresses, honestly
labeled, until the trim-line/derivation ADR re-kinds them — at which point
a cross-kind alias reason (`re_kind`) is added by that ADR's migration,
deliberately not shipped speculatively here.

## Flagged revisions of settled text

1. **Charter route map**: canonical generation route becomes
   `/makes/<company>/generations/<slug>` (model-nested demoted to
   non-canonical, non-durable contextual form); leaf segment fixed as
   `/cars/<slug>` with `?cars=`; `-or-id` removed; `/lines/` and `/codes/`
   segments reserved.
2. **ADR 0007 §7**: the QID-suffix collision rule is replaced by
   flag-never-suffix + curated pins (executing the 2026-07-29 ruling);
   §1's ascending-QID processing order survives as operational determinism
   but no longer assigns any address or bare-slug tenure for newly formed
   clusters.
3. **ADR 0017 §4 naming rule**: stripped nameplate replaces unstripped
   `model.name`; one display grammar for both mint sites; conformance
   guard added.
4. **`epa_attach`'s collision counter**: removed (it was code, never a
   ruling, and it violated ADR 0010's doctrine one level down plus rebuild
   determinism).
5. **`slugify`'s non-ASCII external-id fallback**: replaced by
   flag-and-curate (the same source-neutrality ruling, second application).

Ratified unchanged, to prevent drift: the C5 scope map (per-company
models/lines/generations, global companies/configurations/engines/
transmissions), external-id correspondence discipline (ADR 0011 §4),
evidence-gated placement (ADR 0014), flag-never-suffix for models and
generations (ADR 0010 §2.3, ADR 0017 §4), demote-don't-delete (ADR 0018).

## Out of scope, named

Serving (301s, disambiguation pages, canonical-link headers), FastAPI,
`v_configuration_full`, any page; the engines/platforms/trim-line
grammars; the `re_kind` alias reason and cross-kind arcs; the casefold
natural-key question (PROGRESS Open Questions); the `c124`/`type-124`
probable-duplicate review; per-kind slug-conformance counters in status.py
beyond registry and alias health.

## Consequences

- A rename without an alias row is impossible at the database level, for
  every writer; a freed address can never be silently re-minted; merges
  cannot retire an address without forwarding it. All three properties are
  test-covered, including through raw SQL.
- Stale registry keys abort the consuming pass with the entries named;
  rename scripts refuse to run ahead of their registry rewrites. The
  AMG GT routing and every recorded negative survive any rename
  mechanically.
- After the reviewed `--execute` runs: the 140 QID-suffixed companies
  resolve per the batch (bare where unique and clean, pinned where
  clustered, held where a merge is suspected), no cluster's bare slug
  belongs to anyone, `tesla` and its 179 cars wear honest addresses with
  their old ones aliased, and generation slugs are grammar-conformant with
  their old forms aliased.
- Every generation is addressable at a canonical URL that no data churn
  can move; the read surface can ship against a stable contract.
- Reconciler v16 (mint grammar, collision policy, conformance guards, and
  registry validation all change what runs produce). One migration; one
  new table; two new policy registries and two reserved-set frozensets;
  two decision scripts behind the standing dry-run gate.
