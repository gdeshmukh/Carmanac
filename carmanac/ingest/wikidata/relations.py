"""Land corporate-structure claims for every company we hold (ADR 0022).

A second sweep keyed by the company QIDs already in `external_ids`, landed
under its own marker so the makes sweep's content hashes stay untouched. Per
company: its parent organizations, its subsidiaries, and what it is owned by,
each statement with rank and start/end qualifiers. The pass decides what
asserts (parents and subsidiaries) and what is context only (owned by).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from carmanac.db.models import ExternalId, Source
from carmanac.ingest.landing import LandResult, content_hash, get_source, upsert_raw_records
from carmanac.ingest.wikidata.client import SparqlClient

SOURCE_NAME = "Wikidata"
SWEEP_MARKER = "company_relations"
BATCH_SIZE = 300

# Statement-level so qualifiers ride along: P749 parent organization, P355
# subsidiary (the same edge stated from the parent), P127 owned by. Rank is
# landed, not filtered - the pass drops deprecated statements.
RELATIONS_QUERY = """
SELECT ?item ?edge ?target ?targetLabel ?rank ?start ?end WHERE {{
  VALUES ?item {{ {values} }}
  VALUES (?p ?ps ?edge) {{
    (p:P749 ps:P749 "parents")
    (p:P355 ps:P355 "subsidiaries")
    (p:P127 ps:P127 "owners")
  }}
  ?item ?p ?statement .
  ?statement ?ps ?target ; wikibase:rank ?rank .
  OPTIONAL {{ ?statement pq:P580 ?start . }}
  OPTIONAL {{ ?statement pq:P582 ?end . }}
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "en,mul,de,ja,fr,it".
    ?target rdfs:label ?targetLabel .
  }}
}}
"""


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def target_qids(session: Session) -> list[str]:
    """Every Wikidata identity attached to a company. Groups are model-less,
    and the subsidiary edge lives on them, so the population is not narrowed
    to companies that hold models."""
    qids = set(
        session.scalars(
            select(ExternalId.external_id)
            .join(Source, Source.id == ExternalId.source_id)
            .where(
                Source.name == SOURCE_NAME,
                ExternalId.company_id.isnot(None),
                ExternalId.external_id.regexp_match(r"^Q[0-9]+$"),
            )
            .distinct()
        )
    )
    return sorted(qids, key=lambda q: int(q[1:]))


def _payloads(qids: list[str], bindings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    claims: dict[str, dict[str, dict[tuple[str, str], dict[str, Any]]]] = {
        qid: {"parents": {}, "subsidiaries": {}, "owners": {}} for qid in qids
    }
    for binding in bindings:
        qid = _qid(binding["item"]["value"])
        edge = binding.get("edge", {}).get("value")
        target = binding.get("target", {}).get("value", "")
        if qid not in claims or edge not in claims[qid] or not target.startswith("http"):
            continue
        target_qid = _qid(target)
        rank = _qid(binding["rank"]["value"]).removesuffix("Rank").lower()
        claim = claims[qid][edge].setdefault(
            (target_qid, rank),
            {
                "qid": target_qid,
                "label": binding.get("targetLabel", {}).get("value") or target_qid,
                "rank": rank,
                "starts": set(),
                "ends": set(),
            },
        )
        for binding_name, key in (("start", "starts"), ("end", "ends")):
            value = binding.get(binding_name, {}).get("value")
            if value:
                claim[key].add(value)

    payloads: dict[str, dict[str, Any]] = {}
    for qid in qids:
        payload: dict[str, Any] = {"sweep": SWEEP_MARKER, "qid": qid}
        for edge, by_key in claims[qid].items():
            rows = [
                {
                    **{k: v for k, v in claim.items() if not isinstance(v, set)},
                    "starts": sorted(claim["starts"]),
                    "ends": sorted(claim["ends"]),
                }
                for claim in by_key.values()
            ]
            rows.sort(key=lambda row: (int(row["qid"][1:]), row["rank"]))
            payload[edge] = rows
        payloads[qid] = payload
    return payloads


def land_relations(session: Session, client: SparqlClient | None = None) -> LandResult:
    source = get_source(session, SOURCE_NAME)
    qids = target_qids(session)
    owns_client = client is None
    client = client or SparqlClient()
    try:
        payloads: dict[str, dict[str, Any]] = {}
        for start in range(0, len(qids), BATCH_SIZE):
            batch = qids[start : start + BATCH_SIZE]
            values = " ".join(f"wd:{qid}" for qid in batch)
            bindings = client.query(RELATIONS_QUERY.format(values=values))["results"]["bindings"]
            payloads.update(_payloads(batch, bindings))
    finally:
        if owns_client:
            client.close()

    rows = [
        {
            "source_id": source.id,
            "url": client.endpoint,
            "external_id": qid,
            "http_status": 200,
            "content_hash": content_hash(payload),
            "payload": payload,
        }
        for qid, payload in payloads.items()
    ]
    inserted = upsert_raw_records(session, rows)
    return LandResult(fetched=len(rows), inserted=inserted)


if __name__ == "__main__":
    from carmanac.runner import run

    run(land_relations)
