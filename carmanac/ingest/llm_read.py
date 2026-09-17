"""Ask a model to read one landed nameplate article, and land its answer as
a raw record of the LLM read source (ADR 0017, amended 2026-09-17).

    python -m carmanac.ingest.llm_read porsche/911 [--llm <id>] [--force]

One call per page. The answer lands untouched - raw data is never
discarded - and the gate runs here only to tell the operator what would
survive; the pass runs it again against the page record before anything
is minted or placed. A page already read at this prompt version by this
model is not asked again unless forced.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from carmanac.config import settings
from carmanac.db.models import (
    BodyStyle,
    CataloguePeriod,
    Company,
    Configuration,
    Drivetrain,
    ExternalId,
    Generation,
    GenerationModelLink,
    Model,
    RawRecord,
)
from carmanac.ingest.http import IngestHTTPError, PoliteClient
from carmanac.ingest.landing import content_hash, get_source, upsert_raw_records
from carmanac.ingest.wikipedia.fetch import SOURCE_NAME as WIKIPEDIA_SOURCE
from carmanac.reconcile.sources.llm_read import (
    PROMPT_VERSION,
    SOURCE_NAME,
    Leaf,
    build_messages,
    page_text,
    parse_answer,
    verify,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReadResult:
    read: bool
    generations: int = 0
    leaves: int = 0
    dropped: int = 0

    def summary(self) -> str:
        if not self.read:
            return "already read at this prompt version by this model (--force to ask again)"
        return (
            f"read landed: generations={self.generations} leaves={self.leaves} "
            f"dropped={self.dropped}"
        )


def ask_openrouter(messages: list[dict], llm: str) -> str:
    if not settings.openrouter_api_key:
        raise LookupError("CARMANAC_OPENROUTER_API_KEY is not set")
    with PoliteClient(
        min_interval=0,
        timeout=settings.llm_timeout_seconds,
        headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
    ) as client:
        body = client.request(
            "POST",
            settings.openrouter_endpoint,
            json={
                "model": llm,
                "messages": messages,
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
        ).json()
    if "error" in body:
        raise IngestHTTPError(f"{llm}: {body['error']}")
    return body["choices"][0]["message"]["content"]


def candidate_leaves(session: Session, model_id: int) -> list[Leaf]:
    rows = session.execute(
        select(
            Configuration.id,
            CataloguePeriod.start_year,
            Configuration.trim_name,
            BodyStyle.name,
            Drivetrain.name,
            Configuration.engine_displacement_cc,
            Configuration.cylinders,
            Configuration.power_hp,
        )
        .join(CataloguePeriod, Configuration.catalogue_period_id == CataloguePeriod.id)
        .outerjoin(BodyStyle, Configuration.body_style_id == BodyStyle.id)
        .outerjoin(Drivetrain, Configuration.drivetrain_id == Drivetrain.id)
        .where(CataloguePeriod.model_id == model_id)
        .order_by(CataloguePeriod.start_year, Configuration.slug, Configuration.id)
    )
    return [Leaf(*row) for row in rows]


def read_model(
    session: Session,
    pair: str,
    *,
    llm: str | None = None,
    force: bool = False,
    ask: Callable[[list[dict], str], str] = ask_openrouter,
) -> ReadResult:
    llm = llm or settings.llm_model
    company_slug, _, model_slug = pair.partition("/")
    model = session.scalar(
        select(Model).join(Company).where(Company.slug == company_slug, Model.slug == model_slug)
    )
    if model is None:
        raise LookupError(f"no model {pair!r}")
    source = get_source(session, SOURCE_NAME)
    qid = session.scalar(
        select(ExternalId.external_id).where(
            ExternalId.model_id == model.id,
            ExternalId.source_id == get_source(session, "Wikidata").id,
            ExternalId.external_id.like("Q%"),
        )
    )
    page = None
    if qid is not None:
        page = session.scalar(
            select(RawRecord)
            .where(
                RawRecord.source_id == get_source(session, WIKIPEDIA_SOURCE).id,
                RawRecord.external_id == f"article:{qid}",
            )
            .order_by(RawRecord.last_seen_at.desc(), RawRecord.id.desc())
            .limit(1)
        )
    if page is None:
        raise LookupError(f"{pair} has no landed article to read")
    key = f"read:{qid}"
    prior = session.scalars(
        select(RawRecord).where(RawRecord.source_id == source.id, RawRecord.external_id == key)
    )
    if not force and any(
        (r.payload.get("page_record_id"), r.payload.get("prompt_version"), r.payload.get("llm"))
        == (page.id, PROMPT_VERSION, llm)
        for r in prior
    ):
        return ReadResult(read=False)

    held = [
        (g.name or g.slug or "", g.chassis_codes or [], g.start_year, g.end_year)
        for g in session.scalars(
            select(Generation)
            .join(GenerationModelLink, GenerationModelLink.generation_id == Generation.id)
            .where(
                GenerationModelLink.model_id == model.id,
                GenerationModelLink.superseded_by.is_(None),
            )
            .order_by(Generation.start_year.nulls_last(), Generation.id)
        )
    ]
    leaves = candidate_leaves(session, model.id)
    text = page_text(page.payload.get("wikitext", ""))
    answer = ask(build_messages(page.payload["title"], text, held, leaves), llm)
    payload = {
        "qid": qid,
        "title": page.payload["title"],
        "page_record_id": page.id,
        "revid": page.payload.get("revid"),
        "prompt_version": PROMPT_VERSION,
        "llm": llm,
        "leaf_ids": [leaf.id for leaf in leaves],
        "answer": answer,
    }
    upsert_raw_records(
        session,
        [
            {
                "source_id": source.id,
                "url": page.url,
                "external_id": key,
                "content_hash": content_hash(payload),
                "payload": payload,
            }
        ],
    )
    session.commit()
    verified = verify(parse_answer(answer), text, {leaf.id: leaf for leaf in leaves})
    for item in verified.dropped:
        log.info("dropped: %s", item)
    return ReadResult(True, len(verified.generations), verified.leaves, len(verified.dropped))


if __name__ == "__main__":
    from carmanac.runner import run

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", help="company-slug/model-slug of a model with a landed article")
    parser.add_argument("--llm", help=f"OpenRouter model id (default {settings.llm_model})")
    parser.add_argument("--force", action="store_true", help="ask again even if already read")
    args = parser.parse_args()
    run(read_model, pair=args.model, llm=args.llm, force=args.force)
