"""Reviewed policy registries for the reconciler (ADR 0007 §3-§6).

Everything here is a *decision*, version-controlled and changed via PR. A
policy edit is applied by re-running the reconciler over the raw records
already on disk - re-reconciliation is the normal case, not the exception.

The admission lists were drafted from a live class survey: every distinct P31
class across the landed Wikidata records, resolved to its English label and
classified by hand. QID keys, labels as values - **the labels are
documentation and are never matched against.**

The polarity is deliberate: admission requires AFFIRMATIVE car evidence - a
target class, a builder-type class, or a hand-vetted pin. Under-admission is
the cheap error (edit a list, re-run, the entity appears); over-admission
means unwinding entity rows that other data may already reference.

Version history: docs/progress-archive/reconciler-versions.md.
"""

from __future__ import annotations

# Bump when a policy/mapper/engine change alters what the reconciler would
# produce. `reconciled_records.reconciler_version` records which version
# processed each record, making staleness queryable.
RECONCILER_VERSION = "19"

# --- identity --------------------------------------------------------------

# Curated identity merges (ADR 0007 §5, amended): source entities that are one
# real-world company. Member QID -> canonical QID; the canonical entity's
# record creates the company, members attach their external ids to it. Curated
# and deterministic, never inferred.
#
# Bugatti is the precedent: Wikidata models three corporate eras as three
# entities, but an EB110 is as much a Bugatti as a Type 35 or a Chiron.
# One company, one page.
IDENTITY_MERGES: dict[str, str] = {
    "Q1002267": "Q27401",  # Bugatti Automobili S.p.A. (EB110 era) -> Bugatti (Molsheim)
    "Q2308012": "Q27401",  # Bugatti Automobiles S.A.S. (VW era)   -> Bugatti (Molsheim)
    # --- the company/brand split. One Wikidata editing wave minted bare
    # "<marque> (car brand)" entities (mostly Q124-125xxx) beside the
    # long-standing company entities, giving one real marque two company rows
    # and blocking exact-name matching. Canonical is the substantive
    # company-side entity - it holds the facts and is what model records point
    # at; the brand artifact is the member.
    "Q124982078": "Q26921",  # Alfa Romeo (brand)   -> Alfa Romeo
    "Q124983953": "Q23317",  # Audi (brand)         -> Audi AG
    "Q136368837": "Q23317",  # Audi (Deutsche Automarke artifact) -> Audi AG
    "Q796364": "Q26678",  # BMW (brand)             -> BMW
    "Q42434023": "Q27401",  # Bugatti (brand)       -> Bugatti (Molsheim)
    "Q124966126": "Q27586",  # Ferrari (brand)      -> Ferrari
    "Q139960054": "Q27597",  # Fiat (brand)         -> Fiat
    "Q131548421": "Q1420893",  # Fisker (brand)     -> Fisker
    "Q124966298": "Q9584",  # Honda (brand)         -> Honda
    "Q124966860": "Q35886",  # Lamborghini (brand)  -> Lamborghini
    "Q125097315": "Q35896",  # Lancia (brand)       -> Lancia
    "Q124991042": "Q35962",  # Maserati (brand)     -> Maserati
    "Q125055033": "Q35996",  # Mazda (brand)        -> Mazda
    "Q125130399": "Q20165",  # Nissan (brand)       -> Nissan
    "Q124966804": "Q40993",  # Porsche (brand)      -> Porsche
    "Q125214266": "Q172741",  # Subaru (brand)      -> Subaru
    # Land Rover: Wikidata splits the company (Q35907, holds the facts) from
    # the marque JLR uses today (Q26777551). One make, one page - the Bugatti
    # rule. Canonical is the fact-bearing company entity.
    "Q26777551": "Q35907",
    # The JLR "house of brands" wave: Range Rover, Discovery and Defender are
    # Land Rover model lines, not brands. One Wikidata editing wave minted
    # bare car-brand entities for JLR's marketing repositioning (Q1406xxxxx,
    # class Q10429667 only, no inception/country); the filings disagree  -
    # vPIC files all three under Make=LAND ROVER, EPA likewise. Their only
    # live effect was poisoning the cross-badge guard's brand list
    # (ADR 0013 §3).
    "Q140685136": "Q35907",  # Range Rover (brand artifact) -> Land Rover
    "Q140645228": "Q35907",  # Discovery (brand artifact)   -> Land Rover
    "Q140645257": "Q35907",  # Defender (brand artifact)    -> Land Rover
    # Renault: conglomerate (Q6686, the century-old entity model records
    # point at) vs "Renault S.A.S." (Q98584518, the 2020 legal restructure)
    # vs brand (Q125544573). Corporate-era split of one carmaker - the
    # Bugatti shape - so all three reconcile to Q6686.
    "Q98584518": "Q6686",
    "Q125544573": "Q6686",
    # Tesla, REVERSED substance: the fixture's marque-side pick (Q124981765,
    # materialized as the company) is the factless brand entity, while
    # Tesla, Inc. (Q478214, boilerplate classes, quarantined) holds the
    # founded/country/website facts. Canonical is Tesla, Inc. so the facts
    # project; the brand row's identity (slug, company id) is preserved by
    # the merge script.
    "Q124981765": "Q478214",
    # Consulier: Wikidata splits the brand from Consulier Industries, Warren
    # Mosler's company - the same company/brand shape as the batch above.
    # (Whether Mosler Automotive, the 1993 successor, is an era of the same
    # company is a separate open question - not merged.)
    "Q132560783": "Q17812480",  # Consulier (brand) -> Consulier Industries
    # --- the duplicate-name sweep's approved batch:
    # 11 same-entity pairs from the 105-group exact-name review. Each is one
    # substantive row + a stub whose description identifies the SAME company
    # (mostly the brand-artifact wave). Namesake groups (Ace, Ajax, Caribe,
    # Star OH/IL...) and ambiguous stubs (Imperial, Clipper, the Mazda
    # chinesische-Automarke twin) deliberately stay separate pending
    # cross-source checks. TVR's twin Q80901538 is an assembly PLANT, not a
    # merge candidate - it needs a deny decision, parked.
    "Q135773766": "Q27110",  # Autobianchi (car brand artifact)
    "Q45144846": "Q27377",  # Borgward (Automobilmarke artifact)
    "Q22671448": "Q1002164",  # Bufori (same AU/MY maker, second entity)
    "Q131701890": "Q27460",  # Dacia (brand "owned by Automobile Dacia S.A.")
    "Q125765830": "Q173085",  # De Tomaso (car brand artifact)
    "Q125521978": "Q5463055",  # Flxible (bus brand of the same company)
    "Q3020269": "Q373350",  # Gutbrod (both DE, one company)
    "Q45101510": "Q463827",  # Hansa (car brand artifact)
    "Q58773149": "Q40963",  # Oltcit (Rumänische Automarke artifact)
    "Q1067717": "Q908945",  # Charron-Laycock (one company, two entities)
    # Techrules, REVERSED substance (the Tesla shape): the slug-holding row
    # is the sparse "Automobilmarke"; canonical is the substantive Chinese-
    # manufacturer entity so its facts project. Identity is preserved by the
    # merge script's canonical-not-materialized path... but both ARE
    # materialized here, so the normal path applies: the member row (sparse)
    # collapses into the canonical (substantive).
    "Q63197612": "Q105334279",  # Techrules (marque stub) -> Techrules (manufacturer)
}

# A curated merge is a human identity decision about BOTH sides: naming a
# canonical declares it a company we hold, even where its own classes would
# quarantine (Tesla, Inc. carries only corporate boilerplate). The engine
# admits members and canonicals through this decision.
MERGE_CANONICALS: frozenset[str] = frozenset(IDENTITY_MERGES.values())

# Curated vPIC-make matches (ADR 0008 rung 1): vPIC MakeId -> Wikidata QID,
# for makes the exact-name rung cannot place. Grown exclusively by resolving
# `match_review` flags - each entry is a recorded human judgment, and
# collectively they are the matcher's labeled set.
VPIC_MATCHES: dict[str, str] = {
    # From the ambiguous-match review. Namesake companies are
    # real and stay separate; the pin records which one the vPIC make IS.
    "448": "Q53268",  # TOYOTA -> Toyota Motor (not the Chinese-market brand entity)
    "465": "Q613883",  # MERCURY -> Ford's marque (not the 1914 cyclecar maker)
    "473": "Q35996",  # MAZDA -> Mazda Motor (not the Chinese-market brand entity)
    "474": "Q9584",  # HONDA -> Honda Motor (not the Chinese-market brand entity)
    "476": "Q27564",  # DODGE -> Dodge (not quarantined classless "Dodge" Q134547944)
    # VOLVO -> Volvo Cars, the passenger-car manufacturer. Not the trademark
    # entity (Q20827600, co-owned with Volvo Group for trucks too) and not
    # the Chinese-market brand entity.
    "485": "Q215293",
    # AUDI -> Audi AG. Its label "Audi AG" defeats exact-name matching, and
    # after the brand-artifact merges the only exact-name "AUDI" hit would be
    # SAIC's Chinese AUDI marque (Q136087723, the no-rings brand - a real,
    # separate make) - a wrong unique match this pin preempts.
    "582": "Q23317",
    "12642": "Q112077124",  # SCOUT -> Scout Motors (VW), not the 1900s UK Scout
    # --- the no-match review batch. Four
    # recurring miss shapes: names trigram cannot see (RUF vs "Ruf
    # Automobile"), quarantined records the candidate search never surfaces
    # (KARMA, KANDI - these corroborate-create on match), wrong-namesake
    # traps where the top candidate is a 1910s company (STERLING, AMERICAN
    # MOTORS), and second MakeIds for already-matched companies (HUMVEE,
    # GLICKENHAUS, CONSULIER GTP).
    "496": "Q165708",  # RAM -> Ram Trucks
    "539": "Q1165625",  # MORGAN -> Morgan Motor Company (GB; Q58159151 is a US namesake)
    "1077": "Q27483",  # DAEWOO -> Daewoo Motors
    "1124": "Q202708",  # AMERICAN MOTORS -> AMC (0.8 candidate is a 1910s namesake)
    "1755": "Q1359036",  # TH!NK -> Think Global (the '!' defeats normalization)
    "1777": "Q1934630",  # CODA -> Coda Automotive
    "1991": "Q27423",  # BYD -> BYD Auto
    "2018": "Q17006727",  # KANDI -> Kandi Technologies (quarantined; corroborates)
    "2146": "Q848059",  # MAHINDRA -> Mahindra & Mahindra (the '&' defeats exact)
    "3394": "Q12062242",  # THE VEHICLE PRODUCTION GROUP -> Vehicle Production Group
    "4220": "Q746256",  # PANOZ -> Panoz Auto Development
    "4410": "Q97353704",  # SOLECTRIA -> Solectria Corporation (pinned fetch)
    "4764": "Q1806804",  # MOSLER -> Mosler Automotive
    "5083": "Q21451523",  # GENESIS -> Genesis Motor
    "5122": "Q22671741",  # KARMA -> Karma Automotive (quarantined; corroborates)
    "5464": "Q732935",  # ASUNA -> Asuna (the umlaut in "Asüna" defeats exact)
    "5555": "Q20827172",  # STERLING MOTOR CAR -> Sterling, the Rover-era US brand
    "5557": "Q17812480",  # CONSULIER GTP -> Consulier Industries (GTP is the model)
    "5938": "Q562261",  # PANTHER -> Panther Westwinds (Lima/Kallista, sold in US)
    "7477": "Q1383437",  # EXCALIBUR AUTOMOBILE CORP -> Excalibur (Brooks Stevens)
    "8549": "Q117381875",  # MOKE -> Moke America (the US EV company; Intl is UK/EU)
    "9250": "Q1969669",  # VECTOR AEROMOTIVE -> Vector Motors (renamed, same company)
    "9401": "Q119469",  # CLENET -> Clénet Coachworks (the accent defeats exact)
    "9759": "Q108187217",  # SCUDERIA CAMERON GLICKENHAUS (SCG)
    "9760": "Q295481",  # HUMVEE -> AM General (second MakeId)
    "10393": "Q108187217",  # GLICKENHAUS -> SCG (second MakeId)
    "10919": "Q28027517",  # LUCID -> Lucid Motors
    "11792": "Q28225588",  # ALLARD MOTOR WORKS -> Allard Motor (the Montreal continuation)
    "11832": "Q1045210",  # SHELBY -> Carroll Shelby International (the Cobra's page)
    "11921": "Q7334368",  # RIMAC -> Rimac Automobili
    "12360": "Q108757682",  # INEOS -> Ineos Automotive (pinned fetch)
    "13024": "Q119469",  # CLENET COACHWORKS -> Clénet Coachworks
    "13025": "Q209374",  # CHECKER -> Checker Motors Corporation
    "13150": "Q131553501",  # TELO -> Telo Trucks
    "13765": "Q265465",  # SANTANA -> Santana Motor
    "13766": "Q265465",  # LAND ROVER SANTANA -> Santana Motor (licensee-built)
    "13771": "Q134100360",  # SLATE -> Slate Auto
}

# Curated same-nameplate merges at the model level (ADR 0010 §2.3's "merge"
# resolution): member `model:<ModelId>` -> the canonical `model:<ModelId>`
# whose row it attaches to. The models pass consults this before minting, so
# a rebuild from raw reproduces the judgment mechanically instead of
# re-opening the collision flag a human already answered. Both sides are
# vPIC identifiers, never slugs (ADR 0019). Grown by resolving model-level
# `slug_collision` flags as merges.
VPIC_MODEL_MERGES: dict[str, str] = {
    # The Santana verdict (2026-07-30): both its MakeIds filed the same three
    # licensee-built wheelbase models - one page per wheelbase, LAND ROVER
    # SANTANA's ModelId attaches to the row its SANTANA twin created.
    "model:36864": "model:36863",  # 110" WB
    "model:36866": "model:36865",  # 90" WB
    "model:37552": "model:37551",  # 88" WB
}

# Curated EPA-make matches (ADR 0014 §3): normalized EPA make string ->
# Wikidata QID, for the ~1% of EPA rows whose make string the vPIC make-name
# bridge cannot place (Scion, "McLaren Automotive", "American Motors
# Corporation"...). Grown exclusively by resolving the epa_attach pass's
# `unbridged_make` flags - each entry is a recorded human judgment, the
# VPIC_MATCHES species one bridge over. Starts empty on purpose: the tail is
# reviewed, never guessed.
EPA_MAKE_MATCHES: dict[str, str] = {}

# Curated Wikidata-model matches (ADR 0012, the ADR 0008 registry precedent
# one level down): QID -> our model's vPIC external id, for model entities
# the mechanical rungs cannot place. Grown exclusively by resolving
# model-level `match_review` flags; each entry is a recorded human judgment,
# named in a trailing comment the way VPIC_MATCHES names its makes.
#
# BOTH sides of the judgment are source identifiers, never addresses: a slug
# is a page's name and may be re-chosen at any time, and a judgment that
# stops matching when a page is renamed is a judgment that silently unmakes
# itself - for the negative registry, back into the exact match a human
# rejected.
WIKIDATA_MODEL_MATCHES: dict[str, str] = {}

# The negative-match registry: recorded human judgments that an entity is
# NOT one of our rows, so a dismissed candidate can never silently re-match
# on the next run - before this, negative judgments lived only in policy
# comments and the labeled set had no negative examples. (QID, vPIC external
# id) pairs; consulted before any rung-3 accept and when building flag
# candidates. Grown by flag resolutions, like the positive registry.
WIKIDATA_MODEL_NEGATIVES: frozenset[tuple[str, str]] = frozenset()

# --- Wikidata model fill (ADR 0012 §7) ----------------------------------------

# Companies whose sweep entities may MINT `models` rows. vPIC and EPA are US
# registries: a marque that never sold there can never earn a model row from
# them, so its entities wait forever and its page shows an empty catalogue.
# Listing a company here records the ruling that its waits_unmatched pool is
# real nameplates worth holding at model grain - the census gets reviewed
# before every addition, and the per-entity conditions in the pass (sole
# maker, no membership evidence, no foreign-brand label, no excluded word,
# uncontested slug) still hold each mint to account.
#
# Keys are company QIDs, resolved through external_ids like every maker -
# an alias QID of a listed company gates the same. Values name the company
# the way VPIC_MATCHES names its makes: documentation, never matched.
WIKIDATA_MINT_COMPANIES: dict[str, str] = {
    "Q6746": "citroen",
    "Q29637": "skoda-auto",
    "Q188217": "seat",
    "Q27460": "dacia",
    "Q26823": "abarth",
    "Q8352675": "cupra",
    "Q16040593": "ds-automobiles",
    "Q26944": "alpine",
    "Q59187": "vauxhall",
    "Q6686": "renault",  # overlap probe: 5 as-filed US models beside ~190 EU entities
    "Q40966": "opel",  # overlap probe: 6 as-filed models, no configurations
    "Q35896": "lancia",  # overlap probe: 3 as-filed models
}

# Words that hold an entity out of the mint, matched on word boundaries over
# the label and description together. Concept cars, prototypes and race-only
# cars are an open scope question in the charter; until it is ruled they
# wait rather than mint.
WIKIDATA_MINT_EXCLUDE: tuple[str, ...] = (
    "concept",
    "prototype",
    "race",
    "racing",
    "rally",
    "rallying",
    "formula",
    "le mans",
    "show car",
    "one-off",
)

# Curated nameplate-article routings (ADR 0017 §4): QID -> our model's vPIC
# external id, for articles whose per-generation sections
# describe generations contained in that filing's catalogue but whose QID is
# not 1:1-attached to the model - the ruled "link curated" mechanism. Each
# entry is a recorded human judgment; section parsing never fabricates one.
#
# The AMG GT pair is the founding case (review ruling, 2026-08-06): vPIC
# files ONE `AMG GT` whose catalogue holds two genuinely different cars.
# Q18011551 (the sports car's nameplate page, held as a line by the
# wd-models pass - its P179 children are trims) and Q50368653 (the 4-Door's
# own two-section nameplate page) both belong in that filing's catalogue.
SECTION_ARTICLE_MODELS: dict[str, str] = {
    "Q18011551": "model:5881",  # Mercedes-Benz AMG GT (the sports car's page)
    "Q50368653": "model:5881",  # Mercedes-Benz AMG GT (the 4-Door's page)
}

# Curated company slugs (ADR 0019 §2): QID -> full slug, for companies whose
# mechanical slug collides with a live, aliased, or reserved address - or has
# no ASCII form. Each entry is a recorded human judgment (the encyclopedia's
# disambiguating parenthetical: place, era, or product - `meteor-detroit`,
# `standard-coventry`); a pin freezes the judgment, so addresses never move
# when a country or founding-year fact is later arbitrated. Grown by
# resolving `namesake_collision` / `needs_curated_slug` admission flags.
COMPANY_SLUG_OVERRIDES: dict[str, str] = {}

# Bare slugs no company may hold (ADR 0019). Two kinds live here: a contested
# namesake cluster's bare base, retired so nobody owns `meteor` by arrival
# order, and the site's own root literals - a company sits at `/<slug>`, so
# anything the frontend answers at the root is an address no company can
# take. Zero live company slugs collide with the literals, so they land as a
# pure guard. Code, not data, so occupation survives any rebuild.
RESERVED_COMPANY_SLUGS: frozenset[str] = frozenset(
    {
        "api",
        "about",
        "cars",
        "compare",
        "engines",
        "makes",
        "search",
        "static",
        "transmissions",
        "_next",
        "favicon.ico",
        "robots.txt",
        "sitemap.xml",
    }
)

# Route segments under /<company>/ that can never be a model or line slug
# (ADR 0019): models own the bare second segment, every other kind lives
# under one of these literals. Checked at mint time; zero live conflicts
# existed when the list landed.
RESERVED_ROUTE_SEGMENTS: frozenset[str] = frozenset(
    {"generations", "lines", "codes", "engines", "transmissions", "platforms", "cars", "compare"}
)

# Wrong-grain generation verdicts (ADR 0018 §1): QID -> verdict slug, for
# Wikidata entities whose P179 membership minted a generation row that a
# human ruling found to be another kind of thing entirely. The registry is
# what makes `scripts/decisions/demote_non_generations.py` stick: the
# wd-models pass re-asserts links from P179 every run, so without this gate
# the next re-run would lawfully resurrect what the script retired. The rows
# and their facts stay - they are real entities of the trim-line/derivation
# kind not yet modeled - but they stop being placement candidates.
#
# Grown exclusively by ruling; each entry is a recorded human judgment.
NOT_A_GENERATION: dict[str, str] = {
    # Ruled 2026-08-07: "GT2 and Targa are not generations." The GT2 is a
    # trim lineage spanning five real 911 generations (its 1993-2019 span
    # drove 213 of the 480 open overlap flags); the Targa is a body style.
    "Q1752875": "trim_lineage",  # Porsche 911 GT2
    "Q124935918": "body_style",  # Porsche 911 Targa
    # Ruled 2026-08-07, from the demotion dry-run review: the GT-Four is a
    # trim lineage - the Celica nameplate's own article carries seven real
    # generations; the Hybrid is a powertrain lineage across Civic
    # generations. NOT here by the same review: the Civic Type R FL5 is a
    # real generation (FL5 is its code, under the type-r filing); the
    # Renault 5 Turbo stays an unruled homologation gray case.
    "Q2437169": "trim_lineage",  # Toyota Celica GT-Four
    "Q1626577": "powertrain_lineage",  # Honda Civic Hybrid
}

# --- powertrain family articles (ADR 0020 amendment §2) ----------------------

# Verdicts for every link target the model-page engine/transmission cells
# name, classified in batch from the 2026-08-14 census and committed only
# after review. Keys are titles normalized exactly as the pass normalizes
# them: casefolded, underscores to spaces, one trailing " engine" or
# " transmission" stripped - so "BMW B38" and "BMW B38 engine" are one key,
# and "Toyota A" names an engine family here and a transmission family below
# without colliding (the external-id prefix keeps the kinds apart).
#
# Values are the maker's company slug, or None where no single maker exists
# in `companies` (joint ventures, suppliers we do not hold) - the family
# then mints with no manufacturer rather than a guessed one.
ENGINE_FAMILY_ARTICLES: dict[str, str | None] = {
    "bmw b37": "bmw",
    "bmw b38": "bmw",
    "bmw b47": "bmw",
    "bmw b48": "bmw",
    "bmw b57": "bmw",
    "bmw b58": "bmw",
    "bmw m43": "bmw",
    "bmw m44": "bmw",
    "bmw m47": "bmw",
    "bmw m50": "bmw",
    "bmw m52": "bmw",
    "bmw m54": "bmw",
    "bmw m57": "bmw",
    "bmw n20": "bmw",
    "bmw n46": "bmw",
    "bmw n47": "bmw",
    "bmw n52": "bmw",
    "bmw n55": "bmw",
    "bmw n57": "bmw",
    "bmw n63": "bmw",
    "bmw s68": "bmw",
    "cadillac high technology": "cadillac",
    "chrysler hemi": "chrysler",
    "chrysler la": "chrysler",
    "chrysler powertech": "chrysler",
    "chrysler slant 6": "chrysler",
    "chrysler world": "chrysler",
    "ferrari f154": "ferrari",
    "ferrari f160": "ferrari",
    "ford dld": "ford",
    "ford ecoboost": "ford",
    "ford sigma": "ford",
    "gm ecotec": "general-motors",
    "gm premium v": "general-motors",  # redirects to Northstar engine series
    "hyundai gamma": "hyundai",
    "hyundai kappa": "hyundai",
    "hyundai nu": "hyundai",
    "hyundai smartstream": "hyundai",
    "hyundai u": "hyundai",
    "jaguar aj-v8": "jaguar",
    "lamborghini v12": "lamborghini",
    "lancia flat-4": "lancia",
    "magnum v10": "dodge",
    "mazda b": "mazda",
    "mazda c": "mazda",
    "mazda diesel engines": "mazda",  # umbrella article, one family entity
    "mazda f": "mazda",
    "mazda k": "mazda",
    "mazda l": "mazda",
    "mazda z": "mazda",
    "mercedes-benz m159": "mercedes-benz",
    "mercedes-benz m176/m177/m178": "mercedes-benz",
    "mercedes-benz m274": "mercedes-benz",
    "mitsubishi 4g9": "mitsubishi-motors",
    "nissan vr": "nissan",
    "porsche v8 engines": "porsche",  # umbrella article, one family entity
    "psa ew/dw": "groupe-psa",
    "psa hdi": "groupe-psa",  # diesel brand article; leniently a family
    "simca type 180": "simca",
    "skyactiv": "mazda",  # brand article covering the Skyactiv engine line
    "toyota a": "toyota",
    "toyota ar": "toyota",
    "toyota c": "toyota",
    "toyota dynamic force": "toyota",
    "toyota gd": "toyota",
    "toyota gr": "toyota",
    "toyota kd": "toyota",
    "toyota l": "toyota",
    "toyota nr": "toyota",
    "toyota s": "toyota",
    "toyota tr": "toyota",
    "toyota zr": "toyota",
    "viper": "dodge",
    "volvo b8444s": "volvo-cars",
    "volvo d5": "volvo-cars",
    "volvo engine architecture": "volvo-cars",
    "volvo modular": "volvo-cars",
    "volvo si6": "volvo-cars",
    "cummins b series": None,  # supplier; no companies row
    "douvrin": None,  # PSA-Renault joint venture
    "prv": None,  # Peugeot-Renault-Volvo joint venture
    "rolls-royce–bentley l-series v8": None,  # shared by both marques
}

TRANSMISSION_FAMILY_ARTICLES: dict[str, str | None] = {
    "ford mtx-75": "ford",
    "multitronic": "audi-ag",  # Audi's CVT line; the title alone names no maker
    "toyota a": "toyota",
    "toyota s": "toyota",
    "zf 8hp": None,  # supplier; no companies row
}

# The never-mint side of the same batch: targets that are real links but not
# powertrain families. Checked before the family registries and the prefix
# rung, so a generic title can never mint no matter whose page links it.
# Verdict slugs are documentation and are never matched against.
NOT_A_POWERTRAIN: dict[str, str] = {
    "flat-4": "layout",
    "i4": "layout",
    "inline-four": "layout",
    "straight-4": "layout",
    "straight-6": "layout",
    "straight-three": "layout",
    "straight-four": "layout",
    "straight-six": "layout",
    "straight-twin": "layout",
    "v10": "layout",
    "v12": "layout",
    "v6": "layout",
    "v8": "layout",
    "vr6": "layout",
    "wankel": "layout",
    "carburetor": "technology",
    "dohc": "technology",
    "dual vvt-i": "technology",
    "jetronic": "technology",
    "multiair": "technology",
    "naturally aspirated": "technology",
    "overhead valve": "technology",
    "pressure wave supercharger": "technology",
    "quad-turbo": "technology",
    "stratified charge": "technology",
    "supercharged": "technology",
    "turbocharger": "technology",
    "turbodiesel": "technology",
    "twin turbo": "technology",
    "twin-turbo": "technology",
    "twin-turbocharged": "technology",
    "variable displacement": "technology",
    "variable geometry turbocharger": "technology",
    "variable-geometry turbocharger": "technology",
    "vvt-i": "technology",
    "automatic": "technology",
    "continuously variable": "technology",
    "dual clutch": "technology",
    "dual-clutch": "technology",
    "manual": "technology",
    "semi-automatic": "technology",
    "diesel": "fuel",
    "petrol": "fuel",
    "flexible-fuel vehicle": "vehicle_class",
    "hybrid electric vehicle": "vehicle_class",
    "list of psa engines": "list_page",
    "list of vm motori engines": "list_page",
    "list of volkswagen group diesel engines": "list_page",
    "list of volkswagen group petrol engines": "list_page",
    "all-wheel drive": "drivetrain",
    "front-wheel drive": "drivetrain",
    "quattro (four wheel drive system)": "drivetrain",
    "rear-wheel drive": "drivetrain",
    "aisin warner": "company_not_family",
    "volkswagen": "company_not_family",
    "north america": "place",
    "cubic centimeter": "unit",
    "getrag powershift 6dct450": "dead_link",  # title no longer exists
}

# --- admission (ADR 0007 §3) ------------------------------------------------

# The classes the fetch targets. Any of these also asserts the `manufacturer`
# role (§4): Wikidata's company/marque split is its own artifact - Pontiac is
# brand-only there yet held WMI 1G2 - so both marque classes map to the one
# role, and `historical car manufacturer` is the same claim about a defunct
# marque. vPIC arbitrates all of them later.
TARGET_CLASSES: dict[str, str] = {
    "Q786820": "automobile manufacturer",
    "Q10429667": "car brand",
    "Q112865922": "historical car manufacturer",
}

# Builder-type classes: the door deliberately left open
# for company types that build on other marques' cars - they admit WITHOUT a
# manufacturer role (ADR 0005: builders are companies, roles come from VIN
# evidence). Kept intentionally tiny; grows only by explicit review, one or
# two company types at most.
BUILDER_CLASSES: dict[str, str] = {
    "Q1734300": "coachbuilder",
}

# Hand-vetted entity pins: marques whose Wikidata classes are pure corporate
# boilerplate - real car companies the class-based rule cannot see. Admission
# only; NO role is pinned, because roles come from source evidence (vPIC
# supplies these, not curation).
PINNED_ADMIT: dict[str, str] = {
    "Q6742": "Peugeot",
    "Q55633247": "Singer Vehicle Design",
    "Q98139925": "Li Auto",
    "Q65129456": "Gordon Murray Automotive",
    "Q29068": "Hispano-Suiza",
    "Q40996": "Praga",
    "Q2110378": "Prince Motor Company",
    "Q758549": "Auburn",
    "Q59186515": "Automobili Pininfarina",
    "Q694506": "Wiesmann",
    # Moke International: a stray `P31: automotive industry`
    # claim puts a DENY class on a real revival manufacturer - the same
    # misclass shape as the Peugeot plant, opposite direction.
    "Q57079249": "Moke International",
}

# Hand-vetted entity denials: the mirror of PINNED_ADMIT, for entities whose
# Wikidata classes AFFIRM car-company-hood wrongly. A pin encodes a human
# review that outranks the class sets; without it a misclassed entity is
# unfixable by list edits (its target class would keep re-admitting it).
PINNED_DENY: dict[str, str] = {
    # Classed `automobile manufacturer`, actually a Peugeot assembly plant
    # ("Montagewerk", defunct 1967) - a facility, which DENY covers by class
    # everywhere Wikidata classes it correctly.
    "Q80901498": "Peugeot assembly plant",
}

# Understood co-classes. These no longer admit anything on their own (v2) -
# their job is review triage: a quarantined entity's flag lists which of its
# classes are genuinely UNKNOWN versus merely insufficient, so working the
# queue starts with the unknowns. Corporate boilerplate, national legal
# forms, adjacent manufacturing, racing operations.
ALLOW_CLASSES: dict[str, str] = {
    # generic corporate
    "Q4830453": "business",
    "Q6881511": "enterprise",
    "Q783794": "company",
    "Q43229": "organization",
    "Q2029841": "organization",
    "Q21980538": "commercial organization",
    "Q13235160": "manufacturer",
    "Q136675338": "manufacturing company",
    "Q136678590": "vehicle manufacturer",
    "Q891723": "public company",
    "Q1589009": "privately held company",
    "Q5621421": "private company",
    "Q658255": "subsidiary company",
    "Q113727727": "foreign subsidiary company",
    "Q1186164": "spin-off company",
    "Q3488683": "sister company",
    "Q489209": "joint venture",
    "Q334453": "division",
    "Q261232": "business unit",
    "Q1752211": "strategic business unit",
    "Q129238": "startup company",
    "Q1395324": "family business",
    "Q55097243": "defunct organization",
    "Q15893266": "former entity",
    "Q778575": "conglomerate",
    "Q206361": "concern",
    "Q197952": "corporate group",
    "Q17326725": "group of companies",
    "Q1956113": "parent organization/unit",
    "Q270791": "state-owned enterprise",
    "Q124343324": "Chinese state-owned enterprise",
    "Q19852185": "Chinese wholly state-owned enterprise",
    "Q109344544": "collectively owned enterprise in China",
    "Q124347905": "foreign Chinese state-owned company (partly)",
    # brand-shaped
    "Q431289": "brand",
    "Q2519914": "brand name",
    "Q167270": "trademark",
    "Q15849571": "sub-brand",
    "Q243731": "Zweitmarke",
    # national legal forms
    "Q134161": "joint-stock company",
    "Q167037": "corporation",
    "Q57655560": "corporation",
    "Q48748864": "corporation in Japan",
    "Q161726": "multinational corporation",
    "Q149789": "limited liability company",
    "Q166280": "S.A.",
    "Q422007": "aktiebolag",
    "Q15042660": "aksjeselskap",
    "Q17375963": "besloten vennootschap",
    "Q17376040": "private limited company",
    "Q6832945": "private company limited by shares",
    "Q5225895": "public limited company",
    "Q33685": "limited company",
    "Q56410106": "limited stock company",
    "Q13641190": "open joint-stock company",
    "Q1480166": "kabushiki gaisha",
    "Q460178": "Gesellschaft mit beschränkter Haftung",
    "Q33134112": "société",
    "Q15648878": "società a responsabilità limitata",
    "Q15648901": "sociedad limitada",
    "Q646164": "general partnership",
    "Q155076": "juridical person",
    "Q106668099": "corporate body",
    "Q1345140": "Entreprise",
    # adjacent manufacturing - still a company that builds vehicles
    "Q2005696": "commercial vehicle manufacturer",
    "Q108460239": "truck manufacturer",
    "Q17027266": "bus manufacturing",
    "Q15081030": "motorcycle manufacturer",
    "Q15081032": "historical motorcycle manufacturer",
    "Q131361364": "motorcycle brand",
    "Q29044175": "bicycle manufacturer",
    "Q117826820": "bicycle brand",
    "Q98579904": "tractor brand",
    "Q936518": "aerospace manufacturer",
    "Q107009743": "aircraft manufacturer",
    "Q2995256": "rail vehicle manufacturer",
    "Q136677591": "military vehicle manufacturer",
    "Q73126803": "boat manufacturing company",
    "Q1734300": "coachbuilder",
    # racing operations
    "Q20074337": "auto racing team",
    "Q10497835": "Formula One team",
    "Q109136128": "Formula One constructor team",
    "Q15648574": "racecar constructor",
    "Q88572252": "Formula One engine constructor",
    "Q20784907": "motorcycle racing team",
}

# Any one of these excludes the entity outright (it waits in raw, no flag):
# the entity is affirmatively something `companies` does not hold - a person,
# a place, a car, a dealer, a service, a media property, an abstract concept.
DENY_CLASSES: dict[str, str] = {
    # not an organisation at all
    "Q5": "human",
    "Q3046146": "married couple",
    "Q4167836": "Wikimedia category",
    "Q20136634": "Wikipedia overview article",
    "Q233327": "list of car brands",
    "Q91615393": "Wikidata item that need to be split",
    "Q1886349": "logo",
    "Q1255441": "telegraphic address",
    "Q74817647": "aspect in a geographic region",
    # abstract concepts / activities, not companies
    "Q268592": "industry",
    "Q190117": "automotive industry",
    "Q57261084": "automotive services industry",
    "Q112124893": "manufacture of motor vehicles",
    "Q20058198": "textile machinery industry",
    "Q19862406": "business activity",
    "Q1914636": "activity",
    "Q2695280": "technique",
    "Q125576630": "welding process",
    "Q798992": "rebadging",
    "Q1047113": "field of study",
    "Q28640": "profession",
    "Q349": "sport",
    "Q11541386": "type of business or company",
    "Q17197366": "type of organization",
    # facilities, not the company that runs them (KINTO-plant class)
    "Q83405": "factory",
    "Q41793764": "car factory",
    "Q47509284": "assembly plant",
    "Q110010174": "automobile engine plant",
    "Q41793775": "Mercedes-Benz factories",
    "Q130640983": "wagon factory",
    "Q46398483": "textile factory",
    "Q1662011": "industrial building",
    "Q811102": "type of building",
    "Q13226383": "facility",
    "Q1530704": "corporate headquarters",
    "Q2094773": "showroom",
    "Q787934": "automobile museum",
    "Q18674739": "event venue",
    # retail / dealers / aftermarket services
    "Q786803": "car dealership",
    "Q21422876": "car dealer",
    "Q10859257": "used car dealership",
    "Q507619": "retail chain",
    "Q99536263": "retailer",
    "Q126793": "retail",
    "Q726870": "brick and mortar",
    "Q65553774": "chain",
    "Q4382945": "online shop",
    "Q3390477": "online marketplace",
    "Q484847": "e-commerce",
    "Q9209474": "auction house",
    "Q132510": "market",
    "Q64027599": "gas station chain",
    "Q47516839": "tire shop",
    "Q47516524": "car parts shop",
    "Q132180130": "car parts shop chain",
    "Q1310967": "automobile repair shop",
    "Q130639613": "automobile repair shop chain",
    "Q130639530": "car wash chain",
    "Q2111762": "machine shop",
    # mobility / financial services (the KINTO class)
    "Q291240": "car rental company",
    "Q27973": "ridesharing company",
    "Q192899": "finance lease",
    "Q5037272": "car finance",
    "Q15839238": "lessor",
    "Q730038": "credit institution",
    "Q3591545": "financial services company",
    "Q1618728": "property management",
    "Q2865305": "trading company",
    "Q867147": "product distribution",
    "Q740752": "transport company",
    "Q37929123": "electric vehicle charging network",
    "Q2169973": "service provider",
    "Q17175443": "recruitment agency",
    "Q122229703": "management consulting company",
    # suppliers (demoted, not admitted - ADR 0007 §3's importer/supplier bucket)
    "Q3477381": "automotive supplier",
    "Q20032039": "tire manufacturer",
    "Q6405028": "automotive part",
    "Q7314295": "reproduction auto part",
    # ownership shells
    "Q219577": "holding company",
    # other industries and media
    "Q35127": "website",
    "Q41298": "magazine",
    "Q1153191": "online newspaper",
    "Q17232649": "news website",
    "Q11033": "mass media",
    "Q1076968": "digital media",
    "Q2085381": "publishing house",
    "Q45400320": "open-access publisher",
    "Q96888669": "academic publisher",
    "Q11396960": "production company",
    "Q7397": "software",
    "Q1058914": "software company",
    "Q756637": "application framework",
    "Q1193246": "widget toolkit",
    "Q605117": "graphical widget",
    "Q782543": "graphical user interface",
    "Q467707": "software development kit",
    "Q193040": "embedded system",
    "Q8513": "database",
    "Q7094076": "online database",
    "Q620615": "mobile app",
    "Q19967801": "online service",
    "Q11012": "robot",
    "Q159172": "open hardware",
    "Q14941854": "oil company",
    "Q124628741": "geospatial company",
    # organisations that are not companies
    "Q3918": "university",
    "Q31855": "research institute",
    "Q178790": "labor union",
    "Q708676": "charitable organization",
    "Q79913": "non-governmental organization",
    "Q163740": "nonprofit organization",
    "Q431603": "advocacy group",
    "Q1666019": "pressure group",
    "Q15911314": "association",
    "Q2178147": "trade association",
    "Q106760956": "engineering society",
    "Q988108": "club",
    "Q4438121": "sports organization",
    "Q1328899": "standards organization",
    "Q816676": "Notified Body",
    "Q1110684": "regulatory college",
    "Q18325460": "501(c)(6) organization",
    "Q2659904": "government organization",
    # events
    "Q57305": "trade fair",
    "Q1156329": "auto show",
    "Q464980": "exhibition",
    "Q15275719": "recurring event",
    "Q60147807": "automobile racing series",
    # the entity is a vehicle or model, not a company
    "Q3231690": "car model",
    "Q90834785": "racing automobile model",
    "Q1420": "car",
    "Q59773381": "automobile model series",
    "Q811701": "model series",
    "Q29048322": "vehicle model",
    "Q23866334": "motorcycle model",
    "Q23039057": "bus model",
    "Q850270": "concept car",
    "Q207977": "prototype",
    "Q1147341": "cyclecar",
    "Q360369": "microcar",
    "Q1054271": "steam car",
    "Q13629441": "electric vehicle",
    "Q474698": "amphibious vehicle",
    "Q193468": "van",
    "Q39495": "tractor",
    "Q752870": "motor vehicle",
    "Q42889": "vehicle",
    "Q193234": "motor scooter",
    "Q174174": "diesel engine",
    "Q11019": "machine",
    "Q23048901": "Fittipaldi FD",
}

ADMIT = "admit"
DENY = "deny"
QUARANTINE = "quarantine"


def classify(classes: frozenset[str] | set[str], external_id: str | None = None) -> str:
    """Apply the admission rule to an entity's full P31 class set.

    Two orderings below are load-bearing rather than arbitrary:

    - **Pins outrank classes**, in both directions. A misclassed target-class
      entity (the Peugeot assembly plant) is otherwise unfixable by list edits,
      since its target class would keep re-admitting it.
    - **Target beats deny.** Ford is `automobile manufacturer` + `holding
      company`, both true - it holds Lincoln. Deny-first excluded it outright.

    Everything unmatched quarantines, including the empty class set: that is
    zero evidence, not vacuous truth.
    """
    if external_id is not None and external_id in PINNED_ADMIT:
        return ADMIT
    if external_id is not None and external_id in PINNED_DENY:
        return DENY
    if any(c in TARGET_CLASSES for c in classes):
        return ADMIT
    if any(c in DENY_CLASSES for c in classes):
        return DENY
    if any(c in BUILDER_CLASSES for c in classes):
        return ADMIT
    return QUARANTINE


# --- plausibility -----------------------------------------------------------

# Benz Patent-Motorwagen. An earlier founding is not impossible - Peugeot
# (1810, coffee mills) and ZAZ (1863) are real - so this flags for review
# rather than rejecting.
FIRST_AUTOMOBILE_YEAR = 1886


def plausibility_issues(
    founded_year: int | None, defunct_year: int | None, current_year: int
) -> list[tuple[str, str]]:
    """Sanity-check projected values. Returns (field_name, reason) per issue.

    Flags suspicion, never blocks projection (§6.4). Only bounds and
    cross-field checks that hold regardless of source belong here  -
    single-source value policy stays in the mappers.
    """
    issues: list[tuple[str, str]] = []
    if founded_year is not None:
        if founded_year < FIRST_AUTOMOBILE_YEAR:
            issues.append(("founded_year", f"predates the automobile ({founded_year})"))
        elif founded_year > current_year + 1:
            issues.append(("founded_year", f"in the future ({founded_year})"))
    if defunct_year is not None:
        if founded_year is not None and defunct_year < founded_year:
            issues.append(("defunct_year", f"defunct {defunct_year} before founded {founded_year}"))
        elif defunct_year > current_year + 1:
            issues.append(("defunct_year", f"in the future ({defunct_year})"))
    return issues


# --- conflict resolution (ADR 0007 §6) ---------------------------------------

# Field affinity: same-tier conflicts resolve to the field's registered
# authoritative source. Keyed by field name, valued by `sources.name`. Inert
# while Wikidata is the only asserter, but the registry exists so registering
# vPIC/EPA is an edit here rather than a logic change.
#
# KNOWN GAP: bare field names conflate `companies.name` with `models.name`.
# Harmless while vPIC is the only source of model names, but this owes an
# entity qualification before a second source asserts on the same field name
# at a different level.
FIELD_AFFINITY: dict[str, str] = {
    "name": "Wikidata",
    "summary": "Wikidata",
    "founded_year": "Wikidata",
    "defunct_year": "Wikidata",
    "country_id": "Wikidata",
    "website": "Wikidata",
}
