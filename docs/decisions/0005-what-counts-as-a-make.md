# ADR 0005 — What counts as a make, and how built/derived vehicles relate to their base

- Status: Proposed
- Date: 2026-07-24
- Resolves: the "coachbuilders" open question in `PROGRESS.md`; informs the
  "defunct/acquired makes" question

## Context

The Wikidata ingest landed 7,222 candidate entities. They are not 7,222 makes.
The set mixes real marques with assembly plants, holding companies, and
mobility-services subsidiaries — "KINTO Europe", a Toyota car-sharing operation,
sits beside BMW. The reconciler cannot promote rows into `makes` until "make" is
defined, so this blocks the next piece of work.

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

## Decision

### 1. A make holds manufacturer status. No exceptions.

**A make is a company that takes manufacturer responsibility for a finished
vehicle** — its own World Manufacturer Identifier and type approval, so the car
is titled under its name rather than someone else's.

Earning a WMI is a real graduation in this industry, and the database records it
as one. The test is chosen because it is *verifiable from data already planned
for ingestion*: NHTSA vPIC (Tier 1) publishes WMI-to-manufacturer mappings, so
make status is checkable rather than argued per marque.

- **Pass:** BMW, Ferrari, Pontiac, Alpina, Ruf, Automobili Pininfarina.
- **Fail:** Singer, Gunther Werks, Murphy, LeBaron — the finished car is titled
  as a Porsche or a Duesenberg.
- **Fail:** assembly plants, holding companies, service subsidiaries (KINTO).

An earlier draft of this ADR admitted Singer as an exception on the grounds that
users search for it. That was wrong: an exception granted to whichever marque
feels prominent enough is not a rule, and the next hard case reopens the whole
argument. Findability is a real requirement, but it is satisfied below without
weakening the definition.

### 2. Builders are first-class, and separate from makes

A `builders` table holds companies that transform vehicles without taking
manufacturer responsibility: restomodders (Singer, Gunther Werks), tuners, and
historical coachbuilders (Murphy, LeBaron, Pininfarina-as-contractor).

A builder that later earns a WMI does not move — it gains a `makes` row, and its
builder row links to it via a nullable `make_id`. Alpina and Ruf legitimately
occupy both roles: they are makes *and* they build on other companies' vehicles.
The graduation is visible in the data rather than requiring a migration.

### 3. One derivation table, with a nullable derived side

    vehicle_derivations
      base_generation_id     -> generations   Porsche 911 (964), BMW 3 Series (G20)
      builder_id             -> builders      Singer, Alpina, Zagato, Murphy
      derived_generation_id  -> generations   NULLABLE
      derivation_type_id     -> derivation_types
      + source_id, scraped_at, confidence, raw_record_id   (it is a claim)

`derived_generation_id` is what encodes the distinction the WMI test draws:

- **NULL** — the finished car stays under the base make. A Singer-built 911 is a
  Porsche 911 built by Singer; a Duesenberg Model J is a Duesenberg bodied by
  Murphy. This is the historical norm and needs no separate vehicle entity.
- **Set** — the builder holds manufacturer status and the result is its own
  vehicle. An Alpina B3 (G20) derives from a BMW 3 Series (G20) but is an
  Alpina.

**Generation, not make** — Zagato proves make-level linking wrong.
**Generation, not configuration** — generations carry chassis codes, which *are*
platform identity, and for coachbuilt one-offs the exact donor trim is often
unknown. Configuration-level precision is additive later.

`derivation_types` is a lookup, consistent with the other dimension tables, so a
new relationship kind is an INSERT rather than a migration:

| type | example |
| --- | --- |
| `coachbuilt` | Duesenberg Model J ← body by Murphy |
| `restomod` | Porsche 911 (964) ← rebuilt by Singer |
| `tuned` | Alpina B3 (G20) ← BMW 3 Series (G20) |
| `rebadged` | Toyota GR86 ← Subaru BRZ |
| `platform_shared` | shared architecture, neither derived from the other |

`rebadged` is not an afterthought — badge engineering is a large real category
(GR86/BRZ, Chevrolet Prizm/Toyota Corolla, most of Stellantis) that otherwise
had no home in the schema.

### 4. This makes the searches work

Because every relationship lands in one table keyed by the *base* generation,
"show me modified 911s" is a single query — and it returns Singer and Gunther
Werks (no WMI, `derived_generation_id` NULL) alongside Ruf (a make, derived side
set). Splitting builders from makes does not split the search.

```sql
SELECT b.name, dt.code
FROM vehicle_derivations d
JOIN builders b          ON b.id = d.builder_id
JOIN derivation_types dt ON dt.id = d.derivation_type_id
JOIN generations g       ON g.id = d.base_generation_id
WHERE g.model_id = :porsche_911
  AND dt.code IN ('restomod', 'tuned');
```

Every company a coachbuilder worked for is the same table read the other way:

```sql
SELECT DISTINCT mk.name
FROM vehicle_derivations d
JOIN generations g  ON g.id = d.base_generation_id
JOIN models m       ON m.id = g.model_id
JOIN makes mk       ON mk.id = m.make_id
WHERE d.builder_id = :zagato;
```

## Consequences

- The reconciler gains a mechanical admission rule for `makes`, unblocking
  promotion of the 7,222 landed entities. Expect the survivors to be far fewer;
  the residue is not discarded, merely not promoted — some of it becomes
  `builders` instead.
- **Findability is preserved without weakening the definition.** Singer gets its
  own page and appears in 911 searches as a builder, while the database stays
  honest that the car is a Porsche.
- The historical coachbuilding norm is handled by the common case
  (`derived_generation_id` NULL) rather than as an exception, so a Duesenberg
  stays a Duesenberg.
- Three new tables (`builders`, `vehicle_derivations`, `derivation_types`) and a
  migration. Not yet implemented — this ADR precedes it.
- Alpina and Ruf appear in both `makes` and `builders`, linked. This is real
  duality, not duplication, but the reconciler must not treat the builder row as
  a second make.
- **Still open:** whether a builder's product line needs its own catalogue entity
  when `derived_generation_id` is NULL. "Singer DLS" and "Duesenberg Model J
  Murphy roadster" are named things buyers recognise, but modelling them as
  configurations under the base make may or may not be sufficient. Deferred
  until real data shows how often it bites.
- **Still open:** whether `platform_shared`, a symmetric relation, is well served
  by a directional table.
