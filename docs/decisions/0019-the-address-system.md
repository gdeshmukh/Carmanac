# ADR 0019 — Slugs are addresses, not identity

- Status: Accepted (2026-08-11) — implemented and applied live in PR #37
- Date: 2026-08-11

## Context

An **address** is the part of a URL that names a thing: `bmw` in `/bmw`, or
`964` in `/porsche/generations/964`.
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

**The route map is the hierarchy, and an address says only what the path does
not.**

```
/bmw                          the company
/bmw/m3                       every M3
/bmw/m3/2004                  that year's M3s
/bmw/m3/2004/convertible-rwd  the car
/bmw/generations/e46          the generation
```

Truncating any address gives the index above it, which is the property that
makes the scheme worth having. Company slugs sit at the root, so root literals
are reserved; models own the bare second segment under a company and every
other kind lives under a literal (`/generations/`, `/lines/`, `/codes/`),
which settles Lamborghini having both a model and a line called Huracán.

**A car's address is the tail only** — trim, then drivetrain when the trim
does not already say it (`AWD` + `awd` is one token, not two). It therefore
needs to be unique only inside its model year, and can only be: the fleet's
23,523 cars compose 2,588 distinct tails, and `fwd` fits 3,161 of them. So
`uq_configurations_slug` becomes `unique (catalogue_period_id, slug)`, which
is exactly what the URL promises. A car with nothing to distinguish it wears
`base`, so a year page is always an index and never also a car.

The de-stutter un-suppresses itself where suppressing would merge two cars in
one year — six model years fleet-wide — so 23,511 of 23,523 addresses depend
on nothing but their own row.

**A generation's address is its bare chassis code.** `/porsche/generations/964`
— which is what people call it, and the nameplate is already in the path. Two
exceptions, both from the data: a code shared by two generations under one
company (Celica and Supra are both A60, Camry and Camry Solara both XV20, nine
such pairs) falls back to `<nameplate>-<code>`, which distinguishes them; and a
generation carrying several codes falls back too, because taking the first
would put array order — a scrape artifact — into a public URL. Generations
stay company-anchored per ADR 0016, since which models one covers is collected
evidence that moves.

**The rest of the grammar.** Companies and models take their name, with the
marque stripped from a nameplate but never down to bare digits, so `Mazda3`
stays `mazda3` rather than becoming `3`. Four shapes are refused outright as
source artifacts that reached a public address: an empty ASCII form, a year
range, a `category-` page title, and a numeral ordinal.

**What has no grammar yet, stated rather than hidden.** The third segment
under a model is a catalogue period, and all 18,751 live periods are US model
years with `start_year = end_year`, so a bare year identifies one row. The
schema permits a production period and a facelift phase to compose the same
segment; neither exists yet, and the rule is owed when the first lands —
along with a phase's own label, which the table has no column for.

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
- On first run over the live database: every one of the 23,523 cars
  re-addresses to its tail, 253 generations take their bare chassis code, and
  122 companies, 5 generations and 1 car end up unaddressed — the contested
  namesakes, the generations whose only name is a year range, and the
  `E350 4Matic`/`E350 4matic` pair, which is duplicate data no token can
  separate. The second run changes nothing.
- A company rename now moves one address instead of 23,523: a car's stored
  address no longer contains its parents. The car's URL still changes, because
  the company is a path segment — but nothing stored has to move for it.
- Two ugly classes are left honest rather than papered over. `/bmw/x5/2002/fwd`
  is the address of a car no source has named beyond its drivetrain, and
  `xDrive` is an option rather than a drivetrain fact. Both want vPIC's Body
  Class and option data — a source question, not a grammar one.

## What this changes in earlier decisions

1. **Charter** — a new invariant (slugs are addresses, not identity), and the
   route map is rewritten: the `/makes/` and `/cars/` prefixes are gone, a
   company sits at the root, and every segment adds only its own contribution.
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
