"""Propose WIKIDATA_DUPLICATE_NAMEPLATES entries from the open duplicate queue.

Read-only, the merge-script discipline: print the evidence per contested
entity and a paste-ready registry dict; a human strikes what should not
resolve (concurrent-market duplicates above all) and commits the rest. Never
applies anything - the wd-models pass's duplicates rung does, deterministically.
"""

from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import select, text

from carmanac.db.models import RawRecord, Source
from carmanac.db.session import SessionLocal
from carmanac.reconcile.sources.wikipedia_infobox import parse_infobox, same_subject
from carmanac.reconcile.sources.wikipedia_sections import parse_article

_PAREN = re.compile(r"\(([^()]+)\)\s*$")
_MARKET_WORDS = re.compile(r"\b(china|india|europe|russia|brazil|mexico|iran)\b", re.IGNORECASE)

FLAGS_SQL = text("""
    SELECT rr.external_id AS qid,
           rr.payload->'itemLabel'->>'value' AS label,
           rf.detail->>'slug' AS slug,
           c.slug AS company
    FROM reconciliation_flags rf
    JOIN raw_scrape.raw_records rr ON rr.id = rf.raw_record_id
    JOIN external_ids em ON em.external_id =
        replace(rr.payload->'makers'->>'value', 'http://www.wikidata.org/entity/', '')
        AND em.company_id IS NOT NULL
    JOIN companies c ON c.id = em.company_id
    WHERE rf.status = 'open'
      AND rf.detail->>'reason' IN ('mint_label_duplicates', 'mint_slug_occupied')
    ORDER BY c.slug, rf.detail->>'slug', rr.external_id
""")


def base_slug(slug: str) -> str:
    base = re.sub(r"-(x{0,1}(ix|iv|v?i{0,3}))$", "", slug)
    base = re.sub(r"-\d{4}(-\d{4})?$", "", base)
    base = re.sub(r"-(1st|2nd|3rd|\d+th)-generation$", "", base)
    return base or slug


def main() -> None:
    with SessionLocal() as session:
        wikipedia = session.scalar(select(Source.id).where(Source.name == "Wikipedia (English)"))
        rows = session.execute(FLAGS_SQL).all()
        groups: dict[tuple[str, str], list] = defaultdict(list)
        for row in rows:
            groups[(row.company, base_slug(row.slug))].append(row)

        proposal: dict[str, str] = {}
        print(f"{len(rows)} contested entities in {len(groups)} groups\n")
        for (company, base), members in sorted(groups.items()):
            print(f"[{company}/{base}]")
            for m in members:
                record = session.scalar(
                    select(RawRecord).where(
                        RawRecord.source_id == wikipedia,
                        RawRecord.external_id == f"article:{m.qid}",
                    )
                )
                span = paren = None
                article = "-"
                title = ""
                if record is not None:
                    title = record.payload.get("title", "")
                    if not same_subject(record.payload.get("requested_title", ""), title):
                        article = "REDIRECT"
                    else:
                        article = "y"
                        top = parse_article(title, record.payload.get("wikitext", "")).top_wikitext
                        span = parse_infobox(title, top).production
                        hit = _PAREN.search(title)
                        paren = hit.group(1) if hit else None
                market = bool(_MARKET_WORDS.search(f"{m.label} {paren or ''}"))
                if market:
                    verdict, note = None, "HOLD: market duplicate (time cannot separate)"
                elif article == "y" and paren is None and span is not None:
                    verdict, note = f"model:{company}/{base}", "plain title, dated lead"
                elif span is not None or paren is not None:
                    verdict, note = f"era:{company}/{base}", "era page"
                else:
                    verdict, note = f"era:{company}/{base}", "NO EVIDENCE YET -> awaits span"
                if verdict:
                    proposal[m.qid] = verdict
                span_s = f"{span.start}-{span.end or 'present'}" if span else "-"
                print(
                    f"  {m.qid:<12} {m.label[:34]:<36} article={article:<8} "
                    f"span={span_s:<14} {note}"
                )
            print()
        print("# paste-ready (strike lines before committing):")
        print("WIKIDATA_DUPLICATE_NAMEPLATES: dict[str, str] = {")
        for qid, verdict in sorted(proposal.items(), key=lambda kv: kv[1]):
            print(f'    "{qid}": "{verdict}",')
        print("}")


if __name__ == "__main__":
    main()
