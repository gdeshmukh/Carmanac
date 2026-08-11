# ADR 0019 — Slugs are addresses, not identity

- Status: Proposed (2026-08-11) — implementation rides this branch
- Date: 2026-08-11

## Context

An **address** is the part of a URL that names a thing: `bmw` in `/makes/bmw`.
It is stored as a slug on each row. No page exists yet — nothing is published,
nothing links in — so every address in the database today has an audience of
zero.

Three things made addresses look more important than they are.

**1. Curated judgments were keyed on them.** Three registries in `policy.py`
record human decisions — "this Wikidata entity is the Highlander", "this one
is *not* the K5" — and they identified our side of the judgment by slug pair,
like `toyota/highlander`. Rename the model and the entry stops matching. For
the negative registry that is worse than losing a note: the rejected match
becomes eligible again, and the reconciler can make exactly the mistake a
human told it not to make, silently.

Two of those three registries hold no entry yet and the third holds two, which
is why this is the cheap moment rather than an emergency: they are where all
the coming queue work lands.

The slug got used because a code registry needs a handle on our rows and
database ids are not reproducible across a rebuild. But our rows carry source
identifiers too — every one of the 1,735 models has a vPIC `model:<id>` — and
two registries in the same file (`IDENTITY_MERGES`, `NOT_A_GENERATION`)
already key on QIDs. The slug-keyed ones were the drift, not the pattern.

**2. 140 companies wore Wikidata's ID in their address.** The real Tesla lived
at `tesla-q124981765`, and 179 of its cars had that string baked into their
own addresses. Ruled against on 2026-07-29 as a source leak; this is where it
gets fixed.

**3. An address collision could stop a row from existing.** The company mint
returned nothing when a slug was taken; the EPA pass skipped materializing the
car. A cosmetic naming clash kept real entities out of the database.

## Decision

**An address is a projection.** It is recomputed from current data on every
run, exactly like the reconciled columns, and converges to a no-op when
nothing moved. `carmanac/reconcile/addressing.py` composes every address and
is the only place that does; `recompute_addresses` re-derives all of them.
Changing how addresses look means editing that grammar and re-running — there
is no rename batch, no migration of stored addresses, and no dry-run gate,
because an address is not a commitment while nothing is published.

The consequence is deliberate: renaming a company re-addresses all of its
cars. That is correct while the audience is zero, and it is the trade that
flips the day pages ship — then addresses freeze and retired ones need
forwarding. **That belongs to the read surface's decision, not this one.**

**Identity never touches a slug.** The three model registries key on the
model's own source id (`model:5881`), named in a trailing comment the way
`VPIC_MATCHES` names its makes. `model_lines` resolve by normalized name
rather than by slug. Both sides of every recorded judgment are now source
identifiers, so no rename can disarm one.

**A row may have no address.** Every slug column on the spine is nullable
(companies, models, model_lines, generations, configurations; engines and
transmissions hold no rows yet and follow when they do). A company whose
name is contested, a generation whose only distinguishing fact is a production
span, a car whose composed address is taken — each exists, carries its facts,
and has `slug IS NULL` until an address is available. Unaddressed rows *are*
the queue; they need no parallel flag, and `status.py` counts them.

**Collisions are decided by evidence, never by arrival order.** When two
companies compose the same address, the one with a filing authority behind it
and more nameplates takes it; ties go to whoever already answers there, since
moving an incumbent for no reason is churn. The loser waits for a
`COMPANY_SLUG_OVERRIDES` pin — a recorded human judgment, encyclopedia style
(`meteor-detroit`), deliberately not computed from country or founding year,
which are facts that get corrected. `RESERVED_COMPANY_SLUGS` holds bases no
company may take.

A company also claims the addresses of the other names it is filed under, so
`audi` is not free for a namesake stub to take while Audi AG sits at
`audi-ag` — it claims without taking, and a pin decides which row answers
there.

This replaces arrival order, which is what the QID suffix was hiding: whoever
was ingested first kept the plain name. It is *not* a claim that the loser is
less real — of the 100 contested clusters only six contain a company with any
nameplates and only four a company with cars, and three of those are contested
by two-statement Wikidata stubs. One query over unaddressed rows is how the
rest get looked at.

**The grammar.** Companies and models take their name. A generation is
`<nameplate> (<codes>)` when chassis codes are known and `<nameplate>
(<ordinal> generation)` otherwise, with the marque stripped — but never to
bare digits, so `Mazda3` stays `mazda3` rather than becoming `3`. A car is
`company-model-year[-trim][-drivetrain]`. Four shapes are refused outright as
source artifacts that reached a public address: an empty ASCII form, a year
range, a `category-` page title, and a numeral ordinal.

**Routes.** The canonical generation address is
`/makes/<company>/generations/<slug>` — company-anchored, matching ADR 0016,
because which models a generation covers is collected evidence that moves. The
car page is `/cars/<slug>`; the mission's own noun is the car, and no public
route speaks schema or carries a database id. Models own the bare second
segment under `/makes/<company>/`; every other kind lives under a reserved
literal (`/generations/`, `/lines/`, `/codes/`), which settles Lamborghini
having both a model and a line called Huracán.

## Consequences

- One implementation of the address grammar. Previously the composition
  existed twice — in the minting pass and in the script that repaired what the
  pass minted — with a docstring admitting the duplication.
- No `slug_aliases` table, no triggers, no advisory lock, no rename scripts.
  Retired addresses are not recorded, because no address has ever been served.
  A redirect map is owed at publication and is the read surface's to design.
- A rename can no longer disarm a recorded judgment, because judgments no
  longer name rows by address.
- Addresses move when their data moves. Anything that links to one before the
  read surface exists will break, which is currently nothing.
- On first run over the live database: `tesla` and its 179 cars lose the QID
  suffix, 1,207 car addresses recompute (hyphenation and renamed parents), 30
  generations take the one grammar, and 122 companies, 5 generations and 1 car
  end up unaddressed — the contested namesakes, the generations whose only
  name is a year range, and one casefold-duplicate car. The second run
  changes nothing.

## What this changes in earlier decisions

1. **Charter** — a new invariant (slugs are addresses, not identity); the
   route map gains `/cars/`, `/generations/`, `/lines/`, `/codes/` and loses
   `-or-id`.
2. **ADR 0007 §7** — the QID suffix on collision is gone, and so is the
   non-ASCII fallback to an external id. Both leaked a source's ID scheme into
   public identity.
3. **ADR 0012** — `WIKIDATA_MODEL_MATCHES` and `WIKIDATA_MODEL_NEGATIVES` key
   on source ids, not slug pairs.
4. **ADR 0017 §4** — `SECTION_ARTICLE_MODELS` likewise; the generation naming
   rule now strips the marque and applies at every site.
5. **ADR 0010 §3** — unchanged, and now the one place a naming clash still
   withholds a row: the vPIC models pass flags rather than creating. It is
   left alone deliberately, because changing it moves data; it is owed the
   same treatment and is noted in PROGRESS.

Unchanged: which addresses are unique per company versus globally, the 1:1
rule for external ids, evidence-gated generation placement, and
demote-don't-delete.
