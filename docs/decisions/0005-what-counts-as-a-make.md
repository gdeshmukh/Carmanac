# ADR 0005 — What counts as a make, and how built/derived vehicles relate to their base

- Status: **Accepted** (2026-07-27)
- Date: 2026-07-24; amended 2026-07-27 before acceptance
- Resolves: the "coachbuilders" open question in `PROGRESS.md`; informs the
  "defunct/acquired makes" question
- Revised by: ADR 0006 (its §2 `builders` table is withdrawn; `companies` + roles
  replace it)
- Amendments at acceptance: the derived side of `vehicle_derivations` is
  redefined as *catalogue placement*, decoupled from the VIN test (§3);
  `builder_id` becomes `company_id` (consequence of ADR 0006);
  `platform_shared` is dropped from `derivation_types`, resolved by a future
  `platforms` entity (§5).

## Context

The Wikidata ingest landed 7,222 candidate entities. They are not 7,222 makes.
The set mixes real marques with assembly plants, holding companies, and
mobility-services subsidiaries — "KINTO Europe", a Toyota car-sharing operation,
sits beside BMW. The reconciler cannot classify rows until "make" is defined, so
this blocks the next piece of work.

Three families of edge case force precision.

**Manufacturers of modified vehicles.** Alpina builds on BMW hardware but is a
registered manufacturer whose cars are titled as Alpina. Ruf is the same shape
against Porsche. Singer and Gunther Werks rebuild customer-owned 911s that keep
their original Porsche VIN — legally modified used cars, commercially distinct
products people search for by name.

**Coachbuilders as contractors.** Pininfarina designed and bodied Ferraris; they
remained Ferraris. Historically this was the norm, not the exception: a
Duesenberg Model J was sold as a rolling chassis and bodied by Murphy, LeBaron
or Derham, and the result is universally called a Duesenberg. Nobody calls it a
Murphy. The coachbuilder is a credit on the car, not its identity.

**Coachbuilders as manufacturers.** Automobili Pininfarina sells the Battista
under its own name; Zagato has sold the Mostro. Here the same company *is* the
make.

The last two show the coachbuilder problem is not really a taxonomy problem. A
body house only raises the "is it a make?" question when it builds and sells its
own car — and when it does, it acquires manufacturer status like anyone else.
The rule below therefore resolves the gray area without a coachbuilder clause.

Separately, derivation cannot be a relationship between *makes*: Zagato bodied
Astons, Ferraris and Lancias, so one builder must attach to many companies.

Between proposal and acceptance, ADR 0006 merged makes and builders into one
`companies` table and moved manufacturer status into `company_role_assignments`.
That changed what the derivation table's derived side has to mean — see §3.

## Decision

### 1. A make holds manufacturer status. No exceptions.

**A make is a company that takes manufacturer responsibility for a finished
vehicle** — it issues its own VINs, holds type approval, and the car is titled
under its name rather than someone else's. (The registrar's form of "issues its
own VINs" is holding a World Manufacturer Identifier, the VIN's first three
characters — which is what NHTSA vPIC publishes.)

Issuing your own VINs is a real graduation in this industry, and the database
records it as one. The test is chosen because it is *verifiable from data
already planned for ingestion*: vPIC (Tier 1) publishes WMI-to-manufacturer
mappings, so make status is checkable rather than argued per marque.

- **Pass:** BMW, Ferrari, Pontiac, Alpina, Ruf, Automobili Pininfarina.
- **Fail:** Singer, Gunther Werks, Murphy, LeBaron — the finished car keeps its
  Porsche or Duesenberg VIN.
- **Fail:** assembly plants, holding companies, service subsidiaries (KINTO).

An earlier draft of this ADR admitted Singer as an exception on the grounds that
users search for it. That was wrong: an exception granted to whichever marque
feels prominent enough is not a rule, and the next hard case reopens the whole
argument. Findability is a real requirement, but it is satisfied below without
weakening the definition.

Under ADR 0006 this test **classifies rather than admits**: nothing is excluded
from the database for failing it. A company passing the test holds the
`manufacturer` role in `company_role_assignments`; one failing it is still a
`companies` row with whatever roles it does hold.

### 2. ~~Builders are first-class, and separate from makes~~ — withdrawn

Superseded by ADR 0006. The proposed `builders` table did not survive review: a
builder page needs everything a make page needs, a builder catalogue needs to
parent models, and dual-role companies (Alpina holds its own VINs *and* builds
on BMW hardware) would need two rows and two pages. There is one `companies`
table; "builder" kinds — `coachbuilder`, `restomodder`, `tuner` — are roles in
`company_role_assignments`, exactly like `manufacturer`.

### 3. One derivation table; the derived side records catalogue placement

    vehicle_derivations
      base_generation_id     -> generations   Porsche 911 (964), BMW 3 Series (G20)
      company_id             -> companies     Singer, Alpina, Zagato, Murphy
      derived_generation_id  -> generations   NULLABLE
      derivation_type_id     -> derivation_types
      + source_id, scraped_at, confidence_score, raw_record_id   (it is a claim)

**`derived_generation_id` answers "does this build have its own catalogue
entry?", not "who issues the VIN?":**

- **NULL** — the build stays under the base make. A customer 964 rebuilt by
  Singer without a product name is a Porsche 911; a Duesenberg Model J bodied by
  Murphy is a Duesenberg. This is the historical norm and needs no separate
  vehicle entity.
- **Set** — the build is catalogued as its own model/generation under the
  builder company. The Alpina B3 (G20) is a generation under Alpina; the
  **Singer DLS is a generation under Singer** — with its own page, specs and
  search presence — that *links to* the Porsche 911 (964) rather than appearing
  as a generation of it.

As originally proposed, the derived side was set exactly when the builder held
manufacturer status — restating §1's test. ADR 0006 broke that coupling, and
review showed it was wrong on its own terms:

- **Legal status is per-company; the derived side is per-build.** Ruf issues its
  own VINs on the CTR, yet a converted customer 911 keeps its Porsche VIN and
  stays a Porsche. One company, both cases — a company-level test cannot encode
  a per-relationship fact.
- **ADR 0006 promises Singer's product lines the same catalogue depth as
  BMW's** (`models.company_id` points at any company, regardless of role). The
  DLS is therefore a model under Singer; forbidding its generation a derivation
  link would orphan it from the 964 and break the very query this table exists
  for.
- **Coupling the two would encode the role table twice.** Whose name is in the
  VIN lives in `company_role_assignments` (§1) and nowhere else. The two axes
  agreeing (a company with catalogued derived vehicles usually holds
  `manufacturer` or `restomodder`) is a reconciler *check*, not a constraint.

**Generation, not make** — Zagato proves make-level linking wrong.
**Generation, not configuration** — generations carry chassis codes, which *are*
platform identity, and for coachbuilt one-offs the exact donor trim is often
unknown. Configuration-level precision is additive later.

Both directions of the relationship are first-class reads of the same row:
child→parent ("what is the DLS built on?") is a lookup by
`derived_generation_id`; parent→children ("show me everything built on the
964") is a lookup by `base_generation_id`. Both columns are indexed.

`derivation_types` is a lookup, consistent with the other dimension tables, so a
new relationship kind is an INSERT rather than a migration:

| type | example |
| --- | --- |
| `coachbuilt` | Duesenberg Model J ← body by Murphy |
| `restomod` | Porsche 911 (964) ← rebuilt by Singer |
| `tuned` | Alpina B3 (G20) ← BMW 3 Series (G20) |
| `rebadged` | Toyota GR86 ← Subaru BRZ |

`rebadged` is not an afterthought — badge engineering is a large real category
(GR86/BRZ, Chevrolet Prizm/Toyota Corolla, most of Stellantis) that otherwise
had no home in the schema.

### 4. This makes the searches work

Because every relationship lands in one table keyed by the *base* generation,
"show me modified 911s" is a single query — and it returns Singer and Gunther
Werks (derived side set, no VINs of their own) alongside Ruf conversions
(derived side NULL). Splitting catalogue placement from legal status does not
split the search.

```sql
SELECT c.name, dt.code,
       d.derived_generation_id IS NOT NULL AS own_entry
FROM vehicle_derivations d
JOIN companies c         ON c.id  = d.company_id
JOIN derivation_types dt ON dt.id = d.derivation_type_id
JOIN generations g       ON g.id  = d.base_generation_id
WHERE g.model_id = :porsche_911
  AND dt.code IN ('restomod', 'tuned');
```

Every company a coachbuilder worked for is the same table read the other way:

```sql
SELECT DISTINCT co.name
FROM vehicle_derivations d
JOIN generations g  ON g.id = d.base_generation_id
JOIN models m       ON m.id = g.model_id
JOIN companies co   ON co.id = m.company_id
WHERE d.company_id = :zagato;
```

### 5. Platform sharing is a different relation, and leaves this table

The proposal included `platform_shared` for architecture siblings and left open
whether a symmetric relation belongs in a directional table. It does not, and
the reason is structural: siblings on a shared platform have **no builder, no
donor car, and no direction**. A Lamborghini Urus was not transformed out of an
Audi SQ8; both are built on MLB Evo. Forcing that through `vehicle_derivations`
means electing an arbitrary "base" sibling and writing a meaningless
`company_id`.

`platform_shared` is therefore dropped from `derivation_types`. The relation it
gestured at resolves to a **future `platforms` entity**: generations point at a
platform (`Urus → MLB Evo ← SQ8/Bentayga/Cayenne/Touareg`), and platforms carry
their own lineage via an `evolved_from` self-reference (`MLB Evo → MLB`) —
matching how the industry itself describes architecture evolution. An entity
beats pairwise edges on volume alone: MLB Evo's ~6 generations are 6 foreign
keys, not 15 edges with a fake direction. Multi-hop traces ("the Urus is,
structurally, an upscale Volkswagen SUV") fall out of the lineage chain.

Which platform a generation belongs to is a **sourced claim like any other** —
sources will disagree ("basically a Q5 underneath" vs. `platform: MLB Evo`), and
such conflicts resolve through the normal machinery: field affinity, tier
precedence, retained raw records. Wikidata's `platform` property (P4243) is the
obvious first source. Deferred to its own ADR when that ingestion is planned;
nothing in `vehicle_derivations` blocks or anticipates it.

## Consequences

- The reconciler gains a mechanical classification rule for the `manufacturer`
  role, unblocking triage of the 7,222 landed entities. Expect far fewer
  manufacturers than landed rows; the residue is not discarded — plants and
  subsidiaries simply hold no roles yet, and builders hold builder roles.
- **Findability is preserved without weakening the definition.** Singer gets its
  own page and full catalogue depth for its product lines, appears in 911
  searches as a builder, and the database stays honest that no Singer issues its
  own VINs.
- The historical coachbuilding norm is handled by the common case
  (`derived_generation_id` NULL) rather than as an exception, so a Duesenberg
  stays a Duesenberg.
- Two new tables (`vehicle_derivations`, `derivation_types`) and a migration —
  `builders` is withdrawn (ADR 0006). Implemented immediately after acceptance.
- The "builder product line" open question **closes**: a named product line
  (Singer DLS) *is* a model/generation under the builder company, linked to its
  donor through this table. No new entity kind is needed.
- The "symmetric relation" open question **closes**: resolved by the future
  `platforms` entity (§5), not by this table.
- Role and catalogue placement can disagree in the data (a company with a
  catalogued derived vehicle but no `manufacturer`/`restomodder` role, or vice
  versa). That is a reconciliation flag to surface, never a constraint to
  enforce — the disagreement is usually a data gap, and both facts are sourced.
