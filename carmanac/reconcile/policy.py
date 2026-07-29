"""Reviewed policy registries for the reconciler (ADR 0007 §3-§6).

Everything here is a *decision*, version-controlled and changed via PR; a
policy edit is applied by re-running the reconciler over the raw records
already on disk (re-reconciliation is the normal case, not the exception).

The admission lists were drafted 2026-07-28 from the live class survey: all
255 distinct P31 classes across the 9,883 current Wikidata records, each
resolved to its English label and classified by hand. QID keys, labels as
values - the labels are documentation, never matched against.

The deliberate polarity (ADR 0007 §3): a class not on either list QUARANTINES
its entities rather than admitting them. Under-admission is the cheap error
(edit a list, re-run, the entity appears); over-admission means unwinding
entity rows other data may reference. Genuinely ambiguous classes - "design
company", "OEM", "shipyard", "factory owner" - are therefore on NEITHER list:
their entities wait in quarantine, and reviewing those flags is how these
lists grow.
"""

from __future__ import annotations

# Bump when a policy/mapper/engine change alters what the reconciler would
# produce; `reconciled_records.reconciler_version` records which version
# processed each record, making staleness queryable.
RECONCILER_VERSION = "1"

# --- identity --------------------------------------------------------------

# Curated identity merges (ADR 0007 §5, amended): source entities that are one
# real-world company. Maps member QID -> canonical QID; the canonical entity's
# record creates the company, members attach their external ids to it.
# Merges are curated and deterministic, never inferred.
#
# Bugatti is the precedent (decided 2026-07-28): Wikidata models three
# corporate eras as three entities, but an EB110 is as much a Bugatti as a
# Type 35 or a Chiron - one company, one page.
IDENTITY_MERGES: dict[str, str] = {
    "Q1002267": "Q27401",  # Bugatti Automobili S.p.A. (EB110 era) -> Bugatti (Molsheim)
    "Q2308012": "Q27401",  # Bugatti Automobiles S.A.S. (VW era)   -> Bugatti (Molsheim)
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

# Vetted co-classes: being one of these does not disqualify an entity.
# Corporate boilerplate, national legal forms, adjacent manufacturing (a
# carmaker that also builds motorcycles/trucks/planes is still a carmaker),
# and racing operations (Ferrari, McLaren).
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


def classify(classes: frozenset[str] | set[str]) -> str:
    """Apply the admission rule to an entity's full P31 class set.

    Precedence, found the hard way on the first live pass:

    1. A TARGET class admits outright. The deny list exists to exclude
       entities that are ONLY services/facilities/etc., not to veto a marque:
       Ford is `automobile manufacturer` + `holding company` (both true - it
       holds Lincoln), Geely carries `transport company`. Deny-first excluded
       both. If Wikidata misclasses a non-marque as a target class, the wrong
       tentative `manufacturer` role is the already-accepted KINTO risk and
       vPIC arbitrates it (ADR 0007 §4/Consequences).
    2. Then any deny class excludes: a dealership that is also a "business"
       is still a dealership.
    3. Then admission requires EVERY class to be understood (allow-listed).
    4. Anything else - including an empty class set, which is zero evidence,
       not vacuous truth - waits in quarantine for review.
    """
    if any(c in TARGET_CLASSES for c in classes):
        return ADMIT
    if any(c in DENY_CLASSES for c in classes):
        return DENY
    if classes and all(c in ALLOW_CLASSES for c in classes):
        return ADMIT
    return QUARANTINE


# --- conflict resolution (ADR 0007 §6) ---------------------------------------

# Field affinity: same-tier conflicts resolve to the field's registered
# authoritative source domain. Keyed by assertion field name; values are
# `sources.name`. Inert while Wikidata is the only source, but §6.2 requires
# the registry to exist so vPIC/EPA registration is an edit here, not logic.
FIELD_AFFINITY: dict[str, str] = {
    "name": "Wikidata",
    "summary": "Wikidata",
    "founded_year": "Wikidata",
    "defunct_year": "Wikidata",
    "country_id": "Wikidata",
    "website": "Wikidata",
}
