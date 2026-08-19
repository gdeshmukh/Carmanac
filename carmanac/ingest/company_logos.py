"""Land company-logo claims from Wikidata and file metadata from Commons.

The population comes from canonical companies that already hold models. Their
Wikidata QIDs are read through `external_ids`; names and slugs never take part.
P154 supplies the company-to-file claim, while the Commons API supplies the
file, rendition and reuse metadata. The two responses land under their own
sources so reconciliation can preserve both provenances (ADR 0021).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from carmanac.config import settings
from carmanac.db.models import ExternalId, Model, Source
from carmanac.ingest.http import PoliteClient
from carmanac.ingest.landing import content_hash, get_source, upsert_raw_records
from carmanac.ingest.wikidata.client import SparqlClient
from carmanac.reconcile import policy

log = logging.getLogger(__name__)

WIKIDATA_SOURCE_NAME = "Wikidata"
COMMONS_SOURCE_NAME = "Wikimedia Commons"
SWEEP_MARKER = "company_logos"
WIKIDATA_BATCH_SIZE = 300
COMMONS_BATCH_SIZE = 50
THUMB_WIDTH = 160

COMPANY_LOGOS_QUERY = """
SELECT ?item ?statement ?rank ?logo ?start ?end ?point WHERE {{
  VALUES ?item {{ {values} }}
  ?item p:P154 ?statement .
  ?statement ps:P154 ?logo ; wikibase:rank ?rank .
  OPTIONAL {{ ?statement pq:P580 ?start . }}
  OPTIONAL {{ ?statement pq:P582 ?end . }}
  OPTIONAL {{ ?statement pq:P585 ?point . }}
}}
"""

_EXTMETADATA_FIELDS = (
    "Artist",
    "Attribution",
    "AttributionRequired",
    "Credit",
    "ImageDescription",
    "LicenseShortName",
    "LicenseUrl",
    "Restrictions",
    "UsageTerms",
)


@dataclass(frozen=True)
class CompanyLogoLandResult:
    qids: int
    files: int
    wikidata_inserted: int
    commons_inserted: int

    def summary(self) -> str:
        return (
            f"qids={self.qids} files={self.files} "
            f"wikidata_inserted={self.wikidata_inserted} "
            f"commons_inserted={self.commons_inserted}"
        )


class CommonsClient(PoliteClient):
    """The imageinfo API shape needed for logo files."""

    def __init__(self, endpoint: str | None = None, **kwargs: Any) -> None:
        self.endpoint = endpoint or settings.wikimedia_commons_api_endpoint
        super().__init__(headers={"Accept": "application/json"}, **kwargs)

    def fetch(self, filenames: list[str]) -> list[dict[str, Any]]:
        response = self.request(
            "GET",
            self.endpoint,
            params={
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "prop": "imageinfo",
                "titles": "|".join(f"File:{name}" for name in filenames),
                "iiprop": "url|size|mime|sha1|extmetadata",
                "iiurlwidth": str(THUMB_WIDTH),
                "iimetadataversion": "latest",
                "iiextmetadatafilter": "|".join(_EXTMETADATA_FIELDS),
            },
        )
        return response.json()["query"]["pages"]


def _qid_sort_key(qid: str) -> int:
    return int(qid[1:])


def target_qids(session: Session) -> list[str]:
    """Wikidata identities needed for companies that currently hold a model."""
    qids = set(
        session.scalars(
            select(ExternalId.external_id)
            .join(Source, Source.id == ExternalId.source_id)
            .where(
                Source.name == WIKIDATA_SOURCE_NAME,
                ExternalId.company_id.isnot(None),
                ExternalId.external_id.regexp_match(r"^Q[0-9]+$"),
                exists(select(Model.id).where(Model.company_id == ExternalId.company_id)),
            )
            .distinct()
        )
    )
    source_qids = {
        policy.COMPANY_LOGO_SOURCE_QIDS[qid]
        for qid in qids
        if qid in policy.COMPANY_LOGO_SOURCE_QIDS
    }
    if source_qids:
        known_source_qids = set(
            session.scalars(
                select(ExternalId.external_id)
                .join(Source, Source.id == ExternalId.source_id)
                .where(
                    Source.name == WIKIDATA_SOURCE_NAME,
                    ExternalId.external_id.in_(source_qids),
                )
            )
        )
        missing = source_qids - known_source_qids
        if missing:
            raise LookupError(
                "company logo source QIDs are not attached in external_ids: "
                + ", ".join(sorted(missing, key=_qid_sort_key))
            )
    return sorted(qids | source_qids, key=_qid_sort_key)


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _filename(uri: str) -> str:
    return unquote(urlparse(uri).path.rsplit("/", 1)[-1])


def _rank(uri: str) -> str:
    return uri.rsplit("#", 1)[-1].removesuffix("Rank").lower()


def _wikidata_payloads(
    qids: list[str], bindings: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    statements: dict[str, dict[tuple[str, str, str], dict[str, Any]]] = {qid: {} for qid in qids}
    for binding in bindings:
        qid = _qid(binding["item"]["value"])
        logo = binding.get("logo", {}).get("value")
        statement_uri = binding.get("statement", {}).get("value")
        rank_uri = binding.get("rank", {}).get("value")
        if qid not in statements or not logo or not statement_uri or not rank_uri:
            continue
        file = _filename(logo)
        key = (statement_uri, file, rank_uri)
        statement = statements[qid].setdefault(
            key,
            {
                "id": statement_uri.rsplit("/", 1)[-1],
                "file": file,
                "rank": _rank(rank_uri),
                "starts": set(),
                "ends": set(),
                "points": set(),
            },
        )
        for binding_name, payload_name in (
            ("start", "starts"),
            ("end", "ends"),
            ("point", "points"),
        ):
            value = binding.get(binding_name, {}).get("value")
            if value:
                statement[payload_name].add(value)

    payloads: dict[str, dict[str, Any]] = {}
    for qid in qids:
        rows = []
        for statement in statements[qid].values():
            rows.append(
                {
                    **{k: v for k, v in statement.items() if not isinstance(v, set)},
                    "starts": sorted(statement["starts"]),
                    "ends": sorted(statement["ends"]),
                    "points": sorted(statement["points"]),
                }
            )
        rows.sort(key=lambda row: (row["file"], row["id"]))
        payloads[qid] = {"sweep": SWEEP_MARKER, "qid": qid, "statements": rows}
    return payloads


def _commons_row(page: dict[str, Any], source_id: int, endpoint: str) -> dict[str, Any]:
    payload = {"sweep": SWEEP_MARKER, "page": page}
    imageinfo = (page.get("imageinfo") or [{}])[0]
    return {
        "source_id": source_id,
        "url": imageinfo.get("descriptionurl") or endpoint,
        "external_id": page["title"],
        "http_status": 200,
        "content_hash": content_hash(payload),
        "payload": payload,
    }


def land_company_logos(
    session: Session,
    sparql_client: SparqlClient | None = None,
    commons_client: CommonsClient | None = None,
) -> CompanyLogoLandResult:
    """Land P154 statements and every referenced Commons file description."""
    wikidata = get_source(session, WIKIDATA_SOURCE_NAME)
    commons = get_source(session, COMMONS_SOURCE_NAME)
    qids = target_qids(session)

    owns_sparql = sparql_client is None
    owns_commons = commons_client is None
    sparql_client = sparql_client or SparqlClient()
    commons_client = commons_client or CommonsClient()

    try:
        payloads: dict[str, dict[str, Any]] = {}
        for start in range(0, len(qids), WIKIDATA_BATCH_SIZE):
            batch = qids[start : start + WIKIDATA_BATCH_SIZE]
            values = " ".join(f"wd:{qid}" for qid in batch)
            bindings = sparql_client.query(COMPANY_LOGOS_QUERY.format(values=values))["results"][
                "bindings"
            ]
            payloads.update(_wikidata_payloads(batch, bindings))

        wikidata_rows = [
            {
                "source_id": wikidata.id,
                "url": sparql_client.endpoint,
                "external_id": qid,
                "http_status": 200,
                "content_hash": content_hash(payload),
                "payload": payload,
            }
            for qid, payload in payloads.items()
        ]
        wikidata_inserted = upsert_raw_records(session, wikidata_rows)
        session.commit()

        filenames = sorted(
            {
                statement["file"]
                for payload in payloads.values()
                for statement in payload["statements"]
            }
        )
        pages: list[dict[str, Any]] = []
        for start in range(0, len(filenames), COMMONS_BATCH_SIZE):
            pages.extend(commons_client.fetch(filenames[start : start + COMMONS_BATCH_SIZE]))
        pages.sort(key=lambda page: page["title"])
        commons_rows = [_commons_row(page, commons.id, commons_client.endpoint) for page in pages]
        commons_inserted = upsert_raw_records(session, commons_rows)
        session.commit()
    finally:
        if owns_sparql:
            sparql_client.close()
        if owns_commons:
            commons_client.close()

    result = CompanyLogoLandResult(
        qids=len(qids),
        files=len(pages),
        wikidata_inserted=wikidata_inserted,
        commons_inserted=commons_inserted,
    )
    log.info(result.summary())
    return result


if __name__ == "__main__":
    from carmanac.runner import run

    run(land_company_logos)
