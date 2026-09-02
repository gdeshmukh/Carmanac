"""Propose brand-artifact merges from the landed corporate-structure claims
(ADR 0022 §4). Dry run only: it prints per-row evidence and the identity
registry lines a ruling would add. Nothing is written here - rulings land in
`IDENTITY_MERGES`, and `merge_duplicate_companies.py` applies them.

The test is the ADR's: a brand artifact (every Wikidata id on the row classed
only brand/trademark) and a company whose name it leads at whitespace-bound
tokens are one enterprise. Two directions are censused:

- the artifact holds the catalogue (Ford, 143 models) and its stated owner or
  parent is a held, model-less company whose name it leads;
- the artifact is model-less and stands beside a model-holding company whose
  name it leads (Volvo beside Volvo Cars) - every such pair is listed with its
  description and country, namesakes included, for the ruling to reject.

Usage:  .venv/bin/python scripts/decisions/propose_brand_merges.py
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select, text

from carmanac.db.models import Company, ExternalId, Source
from carmanac.db.session import SessionLocal
from carmanac.ingest.wikidata.relations import SWEEP_MARKER
from carmanac.reconcile import policy
from carmanac.reconcile.engine import current_records
from carmanac.reconcile.sources import wikidata


def leads(brand: str, company: str) -> bool:
    b, c = brand.casefold().split(), company.casefold().split()
    return len(c) > len(b) and c[: len(b)] == b


def main() -> int:
    with SessionLocal() as session:
        source = session.scalars(select(Source).where(Source.name == wikidata.SOURCE_NAME)).one()
        companies = {c.id: c for c in session.scalars(select(Company))}
        model_counts: dict[int, int] = dict(
            session.execute(
                text("SELECT company_id, count(*) FROM models GROUP BY company_id")
            ).all()
        )
        qids_of: dict[int, list[str]] = defaultdict(list)
        company_of: dict[str, int] = {}
        for qid, company_id in session.execute(
            select(ExternalId.external_id, ExternalId.company_id).where(
                ExternalId.source_id == source.id, ExternalId.company_id.isnot(None)
            )
        ):
            qids_of[company_id].append(qid)
            company_of[qid] = company_id

        mapped = {}
        for record in current_records(session, source.id):
            m = wikidata.map_record(record.payload)
            if m is not None:
                mapped[m.external_id] = m
        relations = {
            r.external_id: r for r in current_records(session, source.id, sweep=SWEEP_MARKER)
        }

        def artifact(company_id: int) -> bool:
            qids = qids_of.get(company_id, [])
            return bool(qids) and all(
                qid in mapped
                and mapped[qid].classes
                and mapped[qid].classes <= policy.BRAND_ARTIFACT_CLASSES
                for qid in qids
            )

        def evidence(company_id: int) -> list[tuple[str, str, int | None]]:
            """(edge, label, other company id) for every owner/parent claim."""
            out = []
            for qid in qids_of[company_id]:
                record = relations.get(qid)
                if record is None:
                    continue
                for edge in ("parents", "owners"):
                    for claim in record.payload.get(edge, []):
                        out.append(
                            (edge, claim.get("label") or claim["qid"], company_of.get(claim["qid"]))
                        )
            return out

        def describe(company_id: int) -> str:
            m = next((mapped[q] for q in qids_of[company_id] if q in mapped), None)
            summary = (
                next((a.value for a in m.assertions if a.field_name == "summary"), "") if m else ""
            )
            country = (
                next((a.value for a in m.assertions if a.field_name == "country_id"), "")
                if m
                else ""
            )
            return f"{summary[:50]!r} {country}"

        def registry_line(member: int, canonical: int) -> str:
            return (
                f'    "{qids_of[member][0]}": "{qids_of[canonical][0]}",'
                f"  # {companies[member].name} (brand artifact) -> {companies[canonical].name}"
            )

        registry: list[str] = []
        print("A. brand artifacts holding the catalogue")
        for cid, company in sorted(companies.items(), key=lambda kv: -model_counts.get(kv[0], 0)):
            if not model_counts.get(cid) or not artifact(cid):
                continue
            for edge, label, other in evidence(cid):
                if other is None or other == cid:
                    continue
                target = companies[other]
                if leads(company.name, target.name):
                    for qid in qids_of[cid]:
                        if policy.IDENTITY_MERGES.get(qid) == qids_of[other][0]:
                            break
                    else:
                        print(
                            f"  MERGE {company.name} ({model_counts[cid]} models) -> "
                            f"{target.name} ({model_counts.get(other, 0)} models) | "
                            f"{edge}: {label} | {describe(other)}"
                        )
                        registry.append(registry_line(cid, other))
                else:
                    print(f"  keep  {company.name} | {edge}: {label} -> parent link (ADR 0022 §1)")

        print("\nB. model-less brand artifacts beside a model-holding company they name")
        holding = [cid for cid in companies if model_counts.get(cid)]
        for cid, company in sorted(companies.items(), key=lambda kv: kv[1].name):
            if model_counts.get(cid) or not artifact(cid):
                continue
            for other in holding:
                target = companies[other]
                if not leads(company.name, target.name):
                    continue
                if any(policy.IDENTITY_MERGES.get(q) in qids_of[other] for q in qids_of[cid]):
                    continue
                edges = (
                    "; ".join(f"{edge}: {label}" for edge, label, _ in evidence(cid))
                    or "no owner/parent claims"
                )
                print(
                    f"  MERGE? {company.name} [{describe(cid)}] beside {target.name} "
                    f"({model_counts[other]} models) | {edges}"
                )
                registry.append(registry_line(cid, other))

        print("\nregistry lines for the accepted rows:")
        print("\n".join(registry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
