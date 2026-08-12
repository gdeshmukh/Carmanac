"""Fetch full-article wikitext for nameplate pages (ADR 0017 §4).

Targets are QIDs 1:1-attached to one of our models whose models-sweep record
carries an enwiki sitelink (432 at design time), plus the curated
`SECTION_ARTICLE_MODELS` routings. The full article is fetched - the
per-generation sections live beyond section 0 - and lands untransformed as
`article:<QID>` records beside the section-0 `infobox:` records, the same
Tier 2 archival reasoning: a revision is not re-fetchable once the page
moves on.
"""

from __future__ import annotations

import logging

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from carmanac.ingest.http import PoliteClient
from carmanac.ingest.landing import LandResult, content_hash, get_source, upsert_raw_records
from carmanac.ingest.wikipedia.infoboxes import API_URL, SOURCE_NAME, article_title
from carmanac.reconcile import policy

log = logging.getLogger(__name__)

_COMMIT_EVERY = 25

# Model-attached QIDs with a sitelink, plus curated routings resolved by QID
# alone - the pass re-checks the routing's model at decision time.
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
              AND ei.model_id IS NOT NULL
        )
        OR rr.external_id IN :curated
      )
    ORDER BY rr.external_id
"""


def land_nameplate_articles(session: Session, *, already_landed: bool = True) -> LandResult:
    """Fetch and land one `article:<QID>` record per target nameplate page.

    `already_landed` skips QIDs with a current article record - the
    resumability switch. A refresh run passes False; unchanged wikitext
    lands as a hash-rejected no-op.
    """
    source = get_source(session, SOURCE_NAME)
    targets = session.execute(
        text(_TARGETS_SQL).bindparams(bindparam("curated", expanding=True)),
        # IN () is a syntax error; the impossible sentinel keeps the query
        # valid when the registry is empty.
        {"curated": sorted(policy.SECTION_ARTICLE_MODELS) or ["Q0"]},
    ).all()

    if already_landed:
        landed = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT external_id FROM raw_scrape.raw_records "
                    "WHERE source_id = :sid AND external_id LIKE 'article:%'"
                ),
                {"sid": source.id},
            )
        }
        targets = [t for t in targets if f"article:{t.qid}" not in landed]

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
                    "redirects": "1",
                    "format": "json",
                    "formatversion": "2",
                },
            )
            body = response.json()
            if "error" in body:
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
                    "external_id": f"article:{target.qid}",
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
    return LandResult(fetched=fetched, inserted=inserted, skipped_missing=skipped)


if __name__ == "__main__":
    import sys

    from carmanac.runner import run

    run(land_nameplate_articles, already_landed="--refresh" not in sys.argv[1:])
