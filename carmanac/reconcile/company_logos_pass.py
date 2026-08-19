"""Attach Commons files as QID-exact company logos (ADR 0021)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from carmanac.db.models import (
    ExternalId,
    MediaAsset,
    MediaAttachment,
    RawRecord,
    ReconciliationFlag,
)
from carmanac.ingest.company_logos import (
    COMMONS_SOURCE_NAME,
    SWEEP_MARKER,
    WIKIDATA_SOURCE_NAME,
)
from carmanac.ingest.landing import get_source
from carmanac.reconcile import policy
from carmanac.reconcile.bookkeeping import DecisionLog, mark_reconciled
from carmanac.reconcile.engine import current_records

log = logging.getLogger(__name__)

PASS_NAME = "company_logos"
ROLE = "company_logo"

_ASSET_FIELDS = (
    "kind",
    "mime_type",
    "rendition_url",
    "source_url",
    "title",
    "caption",
    "license",
    "license_url",
    "attribution",
    "rights_notice",
    "width_px",
    "height_px",
    "page_count",
    "byte_size",
    "content_hash",
)


@dataclass
class CompanyLogoStats:
    companies: int = 0
    assets_created: int = 0
    assets_superseded: int = 0
    attachments_created: int = 0
    attachments_superseded: int = 0
    attachments_retired: int = 0
    flags_opened: int = 0
    flags_dismissed: int = 0
    waits_no_logo: int = 0
    waits_ambiguous: int = 0
    waits_metadata: int = 0

    def summary(self) -> str:
        return (
            f"companies={self.companies} "
            f"assets={self.assets_created} (superseded={self.assets_superseded}) "
            f"attachments={self.attachments_created} "
            f"(superseded={self.attachments_superseded}, retired={self.attachments_retired}) "
            f"flags={self.flags_opened} (dismissed={self.flags_dismissed}) "
            f"waits=no_logo:{self.waits_no_logo} ambiguous:{self.waits_ambiguous} "
            f"metadata:{self.waits_metadata}"
        )


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain(value: str | None) -> str | None:
    if not value:
        return None
    parser = _PlainText()
    parser.feed(value)
    text = " ".join(" ".join(parser.parts).split())
    return text or None


def _statement_date(value: str) -> date | None:
    try:
        return datetime.fromisoformat(value.lstrip("+").replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _rank_winners(statements: list[dict[str, Any]]) -> set[str]:
    if any(statement["rank"] == "preferred" for statement in statements):
        statements = [statement for statement in statements if statement["rank"] == "preferred"]
    return {statement["file"] for statement in statements}


def _selected_files(statements: list[dict[str, Any]], as_of: date) -> set[str]:
    current: list[dict[str, Any]] = []
    historical: list[tuple[date, dict[str, Any]]] = []
    for statement in statements:
        if statement.get("rank") not in {"normal", "preferred"} or statement.get("points"):
            continue
        starts = [_statement_date(value) for value in statement.get("starts", [])]
        ends = [_statement_date(value) for value in statement.get("ends", [])]
        if any(value is None for value in starts + ends):
            continue
        if starts and min(starts) > as_of:
            continue
        if ends and max(ends) < as_of:
            historical.append((max(ends), statement))
        else:
            current.append(statement)

    if current:
        return _rank_winners(current)
    if not historical:
        return set()
    latest_end = max(end for end, _ in historical)
    return _rank_winners([statement for end, statement in historical if end == latest_end])


def _file_key(filename: str) -> str:
    return filename.replace("_", " ").strip().casefold()


def _metadata_value(metadata: dict[str, Any], key: str) -> str | None:
    return _plain(metadata.get(key, {}).get("value"))


def _asset_values(record: RawRecord, source_id: int) -> dict[str, Any] | None:
    page = record.payload.get("page", {})
    imageinfo = (page.get("imageinfo") or [None])[0]
    if not imageinfo or not str(imageinfo.get("mime", "")).startswith("image/"):
        return None

    metadata = imageinfo.get("extmetadata") or {}
    license_name = _metadata_value(metadata, "LicenseShortName") or _metadata_value(
        metadata, "UsageTerms"
    )
    attribution = (
        _metadata_value(metadata, "Attribution")
        or _metadata_value(metadata, "Artist")
        or _metadata_value(metadata, "Credit")
    )
    source_url = imageinfo.get("descriptionurl")
    rendition_url = imageinfo.get("thumburl") or imageinfo.get("url")
    file_hash = imageinfo.get("sha1")
    if not all((source_url, rendition_url, file_hash)):
        return None

    return {
        "kind": "image",
        "mime_type": imageinfo.get("mime"),
        "rendition_url": rendition_url,
        "storage_url": None,
        "source_url": source_url,
        "title": str(page.get("title", "")).removeprefix("File:") or None,
        "caption": None,
        "license": license_name,
        "license_url": _metadata_value(metadata, "LicenseUrl"),
        "attribution": attribution,
        "rights_notice": _metadata_value(metadata, "Restrictions"),
        "width_px": imageinfo.get("width"),
        "height_px": imageinfo.get("height"),
        "page_count": imageinfo.get("pagecount"),
        "byte_size": imageinfo.get("size"),
        "content_hash": file_hash,
        "source_id": source_id,
        "raw_record_id": record.id,
        "scraped_at": record.last_seen_at,
    }


def _supersede(session: Session, old: Any, values: dict[str, Any]) -> Any:
    old.superseded_by = old.id
    session.flush()
    successor = type(old)(**values)
    session.add(successor)
    session.flush()
    old.superseded_by = successor.id
    session.flush()
    return successor


def _same_asset(asset: MediaAsset, values: dict[str, Any]) -> bool:
    return all(getattr(asset, field) == values[field] for field in _ASSET_FIELDS)


def _retire_attachment(
    session: Session, attachment: MediaAttachment | None, stats: CompanyLogoStats
) -> None:
    if attachment is None:
        return
    attachment.superseded_by = attachment.id
    session.flush()
    stats.attachments_retired += 1


def run_company_logos_pass(session: Session, as_of: date | None = None) -> CompanyLogoStats:
    """Project current P154 claims into `company_logo` attachments."""
    as_of = as_of or datetime.now(UTC).date()
    wikidata = get_source(session, WIKIDATA_SOURCE_NAME)
    commons = get_source(session, COMMONS_SOURCE_NAME)
    decisions = DecisionLog(session, wikidata.id, PASS_NAME)
    stats = CompanyLogoStats()

    company_by_qid = dict(
        session.execute(
            select(ExternalId.external_id, ExternalId.company_id).where(
                ExternalId.source_id == wikidata.id,
                ExternalId.company_id.isnot(None),
            )
        ).all()
    )
    logo_targets_by_source = {
        source_qid: target_qid for target_qid, source_qid in policy.COMPANY_LOGO_SOURCE_QIDS.items()
    }
    grouped: dict[int, list[RawRecord]] = {}
    curated_target_by_record: dict[int, str] = {}
    for record in current_records(session, wikidata.id, sweep=SWEEP_MARKER):
        target_qid = logo_targets_by_source.get(record.external_id)
        if target_qid is not None:
            company_id = company_by_qid.get(target_qid)
            if company_id is None:
                raise LookupError(
                    f"company logo target QID is not attached in external_ids: {target_qid}"
                )
            grouped.setdefault(company_id, []).append(record)
            curated_target_by_record[record.id] = target_qid
            continue

        canonical_qid = policy.IDENTITY_MERGES.get(record.external_id, record.external_id)
        if canonical_qid != record.external_id:
            decisions.record(
                record,
                "skipped_identity_merge_member",
                method="curated_identity_merge",
                detail={"canonical": canonical_qid},
            )
            mark_reconciled(session, record)
            continue
        if record.external_id in policy.COMPANY_LOGO_SOURCE_QIDS:
            decisions.record(
                record,
                "skipped_logo_source_override",
                method="curated_logo_source_qid",
                detail={"source_qid": policy.COMPANY_LOGO_SOURCE_QIDS[record.external_id]},
            )
            mark_reconciled(session, record)
            continue
        company_id = company_by_qid.get(record.external_id)
        if company_id is None:
            decisions.record(record, "waits_unattached_qid")
            mark_reconciled(session, record)
            continue
        grouped.setdefault(company_id, []).append(record)

    commons_records: dict[str, RawRecord] = {}
    for record in current_records(session, commons.id, sweep=SWEEP_MARKER):
        page_title = record.payload.get("page", {}).get("title", record.external_id or "")
        commons_records[_file_key(str(page_title).removeprefix("File:"))] = record

    live_assets = {
        asset.source_url: asset
        for asset in session.scalars(
            select(MediaAsset).where(
                MediaAsset.source_id == commons.id,
                MediaAsset.superseded_by.is_(None),
            )
        )
    }
    live_attachments = {
        attachment.company_id: attachment
        for attachment in session.scalars(
            select(MediaAttachment).where(
                MediaAttachment.source_id == wikidata.id,
                MediaAttachment.role == ROLE,
                MediaAttachment.company_id.isnot(None),
                MediaAttachment.superseded_by.is_(None),
            )
        )
    }
    open_flags = {
        flag.company_id: flag
        for flag in session.scalars(
            select(ReconciliationFlag).where(
                ReconciliationFlag.kind == "multi_value",
                ReconciliationFlag.field_name == ROLE,
                ReconciliationFlag.source_id == wikidata.id,
                ReconciliationFlag.company_id.isnot(None),
                ReconciliationFlag.status == "open",
            )
        )
    }

    for company_id in sorted(grouped):
        stats.companies += 1
        records = sorted(grouped[company_id], key=lambda record: int(record.external_id[1:]))
        evidence: dict[str, list[RawRecord]] = {}
        for record in records:
            for filename in _selected_files(record.payload.get("statements", []), as_of):
                evidence.setdefault(filename, []).append(record)

        candidates = sorted(evidence)
        approved_files = {
            policy.COMPANY_LOGO_FILES[record.external_id]
            for record in records
            if record.external_id in policy.COMPANY_LOGO_FILES
        }
        eligible_approved_files = approved_files.intersection(evidence)
        if len(eligible_approved_files) > 1:
            raise ValueError(f"conflicting company logo choices: {sorted(eligible_approved_files)}")
        approved_file = next(iter(eligible_approved_files), None)
        if approved_file is not None:
            candidates = [approved_file]

        live_attachment = live_attachments.get(company_id)
        flag = open_flags.get(company_id)
        if not candidates:
            _retire_attachment(session, live_attachment, stats)
            stats.waits_no_logo += 1
            outcome = "waits_no_company_logo"
        elif len(candidates) > 1:
            _retire_attachment(session, live_attachment, stats)
            detail = {
                "candidates": [
                    {
                        "file": filename,
                        "qids": [record.external_id for record in evidence[filename]],
                    }
                    for filename in candidates
                ]
            }
            raw_record = evidence[candidates[0]][0]
            if flag is None:
                flag = ReconciliationFlag(
                    kind="multi_value",
                    company_id=company_id,
                    field_name=ROLE,
                    detail=detail,
                    source_id=wikidata.id,
                    raw_record_id=raw_record.id,
                )
                session.add(flag)
                open_flags[company_id] = flag
                stats.flags_opened += 1
            else:
                flag.detail = detail
                flag.raw_record_id = raw_record.id
            stats.waits_ambiguous += 1
            outcome = "flagged_multiple_company_logos"
        else:
            filename = candidates[0]
            commons_record = commons_records.get(_file_key(filename))
            values = _asset_values(commons_record, commons.id) if commons_record else None
            if commons_record is not None:
                mark_reconciled(session, commons_record)
            if values is None:
                _retire_attachment(session, live_attachment, stats)
                stats.waits_metadata += 1
                outcome = "waits_commons_metadata"
            else:
                asset = live_assets.get(values["source_url"])
                if asset is None:
                    asset = MediaAsset(**values)
                    session.add(asset)
                    session.flush()
                    stats.assets_created += 1
                elif not _same_asset(asset, values):
                    values["storage_url"] = asset.storage_url
                    asset = _supersede(session, asset, values)
                    stats.assets_superseded += 1
                live_assets[asset.source_url] = asset

                attachment_values = {
                    "media_asset_id": asset.id,
                    "company_id": company_id,
                    "role": ROLE,
                    "source_id": wikidata.id,
                    "raw_record_id": evidence[filename][0].id,
                    "scraped_at": evidence[filename][0].last_seen_at,
                }
                if live_attachment is None:
                    live_attachment = MediaAttachment(**attachment_values)
                    session.add(live_attachment)
                    session.flush()
                    stats.attachments_created += 1
                elif live_attachment.media_asset_id != asset.id:
                    live_attachment = _supersede(session, live_attachment, attachment_values)
                    stats.attachments_superseded += 1
                live_attachments[company_id] = live_attachment
                outcome = "company_logo_attached"

        if flag is not None and len(candidates) <= 1:
            flag.status = "dismissed"
            flag.resolved_at = func.now()
            flag.detail = {**(flag.detail or {}), "resolution": outcome}
            stats.flags_dismissed += 1
            open_flags.pop(company_id, None)

        detail = {"files": candidates} if candidates else None
        for record in records:
            target_qid = curated_target_by_record.get(record.id)
            record_detail = dict(detail or {})
            if target_qid is not None:
                record_detail["target_qid"] = target_qid
            if approved_file is not None:
                record_detail["approved_file"] = approved_file
            method = "qid_p154"
            if target_qid is not None:
                method = "curated_logo_source_qid"
            if approved_file is not None:
                method = "curated_logo_file"
            decisions.record(
                record,
                outcome,
                method=method,
                detail=record_detail or None,
            )
            mark_reconciled(session, record)

    decisions.flush()
    session.commit()
    log.info("company logos pass done: %s", stats.summary())
    return stats


if __name__ == "__main__":
    from carmanac.runner import run

    run(run_company_logos_pass)
