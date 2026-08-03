# Working notes

Longer-form background that would bloat the source if it lived inline.

The split with the rest of `docs/`:

- **ADRs** (`docs/decisions/`) record *decisions* — the alternatives weighed,
  the choice, the consequences. They are the durable rationale.
- **These notes** record *background and incident history* — how a source
  actually behaves, what broke once and why the guard against it exists, and
  the tooling explanations worth having written down.
- **Code comments** answer "why does this exist / why is it this way" at the
  point of use, in a line or two, with a pointer here or to an ADR when the
  full story matters.

Nothing here is authoritative over an ADR. When a note and an ADR disagree,
the ADR wins and the note is stale.

| Note | Covers |
| --- | --- |
| [reconciler.md](reconciler.md) | How the reconciler works — start here |
| [schema-traps.md](schema-traps.md) | Supersession, partial unique indexes, `NULLS NOT DISTINCT`, Alembic gotchas |
| [wikidata-fetch.md](wikidata-fetch.md) | SPARQL basics, the three fetch axes, why the queries aggregate |
| [reconciler-incidents.md](reconciler-incidents.md) | Live failures that each put a guard in the code |
