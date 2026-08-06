"""Fetch section-0 wikitext for generation-shaped articles (ADR 0016 §3).

Targets are QIDs from the landed models sweep whose payload carries an
enwiki sitelink and which are generation-relevant: either already attached
to one of our generations, or carrying P179 series membership (the line-case
entities whose time is what the placement pass waits on). The article is the
Tier 2 archival record - a revision is not re-fetchable once the page moves
on, so raw wikitext lands untransformed and parsing stays in the pass.

`infobox:<QID>` external ids keep the record kind readable from the
namespaced id (the 2026-07-29 lesson: kinds are never told apart by payload
shape).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import unquote

from sqlalchemy import text
from sqlalchemy.orm import Session

from carmanac.ingest.http import PoliteClient
from carmanac.ingest.landing import content_hash, get_source, upsert_raw_records

log = logging.getLogger(__name__)

SOURCE_NAME = "Wikipedia (English)"
API_URL = "https://en.wikipedia.org/w/api.php"

# Commit cadence: the sweep is resumable at this granularity (the vPIC
# model-years lesson - a long polite fetch must survive interruption).
_COMMIT_EVERY = 25

_TARGETS_SQL = """
    SELECT rr.external_id AS qid,
           rr.payload->'article'->>'value' AS article_url
    FROM raw_scrape.raw_records rr
    JOIN sources s ON s.id = rr.source_id AND s.name = 'Wikidata'
    WHERE rr.payload->>'sweep' = 'models'
      AND coalesce(rr.payload->'article'->>'value', '') <> ''
      AND (
        EXISTS (
            SELECT 1 FROM external_ids ei
            WHERE ei.external_id = rr.external_id
              AND ei.source_id = rr.source_id
              AND ei.generation_id IS NOT NULL
        )
        OR coalesce(rr.payload->'seriesOf'->>'value', '') <> ''
      )
    ORDER BY rr.external_id
"""


@dataclass(frozen=True)
class InfoboxLandResult:
    fetched: int
    inserted: int
    skipped_missing: int


def article_title(article_url: str) -> str:
    """`https://en.wikipedia.org/wiki/BMW_3_Series_(E30)` -> the page title."""
    return unquote(article_url.rsplit("/wiki/", 1)[-1])


def land_generation_infoboxes(
    session: Session, *, already_landed: bool = True
) -> InfoboxLandResult:
    """Fetch and land one `infobox:<QID>` record per target article.

    `already_landed` skips QIDs that have a current infobox record - the
    resumability switch for the long first sweep. A refresh run (revisions
    move) passes False and re-fetches everything; unchanged wikitext lands
    as a hash-rejected no-op.
    """
    source = get_source(session, SOURCE_NAME)
    targets = session.execute(text(_TARGETS_SQL)).all()

    if already_landed:
        landed = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT external_id FROM raw_scrape.raw_records "
                    "WHERE source_id = :sid AND external_id LIKE 'infobox:%'"
                ),
                {"sid": source.id},
            )
        }
        targets = [t for t in targets if f"infobox:{t.qid}" not in landed]

    fetched = inserted = skipped = 0
    pending: list[dict] = []
    with PoliteClient() as client:
        for i, target in enumerate(targets, start=1):
            title = article_title(target.article_url)
            response = client.request(
                "GET",
                API_URL,
                params={
                    "action": "parse",
                    "page": title,
                    "prop": "wikitext|revid",
                    "section": "0",
                    "redirects": "1",
                    "format": "json",
                    "formatversion": "2",
                },
            )
            body = response.json()
            if "error" in body:
                # A moved-without-redirect or deleted page. The sitelink said
                # it exists; the wiki disagrees. Log and move on - the QID
                # simply contributes no time evidence.
                log.warning(
                    "no article for %s (%s): %s", target.qid, title, body["error"].get("code")
                )
                skipped += 1
                continue
            parse = body["parse"]
            payload = {
                "qid": target.qid,
                "title": parse.get("title", title),
                "requested_title": title,
                "revid": parse.get("revid"),
                "wikitext": parse.get("wikitext", ""),
            }
            pending.append(
                {
                    "source_id": source.id,
                    "external_id": f"infobox:{target.qid}",
                    "url": target.article_url,
                    "payload": payload,
                    "content_hash": content_hash(payload),
                }
            )
            fetched += 1
            if len(pending) >= _COMMIT_EVERY:
                inserted += upsert_raw_records(session, pending)
                session.commit()
                pending = []
                log.info("landed %d/%d articles", i, len(targets))

    if pending:
        inserted += upsert_raw_records(session, pending)
        session.commit()
    return InfoboxLandResult(fetched=fetched, inserted=inserted, skipped_missing=skipped)
