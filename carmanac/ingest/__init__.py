"""Source ingestion.

One subpackage per source, mirroring `scrapers/<source_name>/` from CLAUDE.md.
The distinction: `scrapers/` is for Scrapy spiders crawling HTML, which run
under Scrapy's own runner. API-based Tier 1 sources - Wikidata SPARQL, the
NHTSA vPIC REST API, EPA bulk CSV - are plain importable Python that needs a
database session, so they live inside the package instead.

Every source follows the same two steps:

    fetch + land -> raw_scrape.raw_records   (source-specific)
    reconcile    -> entities + provenance    (shared, source-agnostic)
"""
