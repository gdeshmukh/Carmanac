"""The coverage fixture: marques the Wikidata fetch MUST land.

PROGRESS.md's risk register said "re-check coverage against a known list
whenever the query changes" - this is that list, made mechanical. It exists
because coverage losses are SILENT: the two-class query lost Pontiac, then
TVR, then Tesla/Peugeot/Li Auto, and nothing failed - the rows simply were
not there (foundation review F6). After every landing run the ingest script
checks this list against the landed set; a miss is a failing exit, not an
archaeology find.

~218 marques spanning every axis the risk register worries about: mainstream
global, defunct US/British/French/Italian/German, JDM + kei, Soviet/Eastern
bloc, Chinese, Korean, Indian, Brazilian, Australian, boutique. Every QID was
resolved via the Wikidata search API and hand-reviewed (2026-07-28); several
were deliberately swapped to the MARQUE-side entity where Wikidata splits
company from brand (Tesla, Eagle, Venturi, Tatra, Cord, DeLorean, Fisker,
Willys).

This fixture covers the FETCH; catalogue identity is a separate, later
question. Wikidata models some single real-world marques as several corporate
-era entities - all three Bugatti entries below reconcile to ONE company
(decided 2026-07-28: an EB110 is as much a Bugatti as a Type 35 or a Chiron),
via the curated identity-merge registry planned for the reconciler's
policy.py. The fixture lists every era entity because each must LAND.

A triaged miss has exactly three legitimate outcomes: fix a fetch axis, pin
the QID in queries.py, or - if Wikidata's entity is unusable - move it to
NOT_IN_WIKIDATA below with a note. Never silently delete an entry.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

# (qid, name) - sorted by name.
KNOWN_MARQUES: tuple[tuple[str, str], ...] = (
    ("Q26823", "Abarth"),
    ("Q26893", "AC Cars"),
    ("Q53097", "Acura"),
    ("Q395941", "Agrale"),
    ("Q26921", "Alfa Romeo"),
    ("Q746273", "Allard"),
    ("Q203550", "Alpina"),
    ("Q26944", "Alpine"),
    ("Q26952", "Alvis Car and Engineering Company"),
    ("Q202708", "American Motors Corporation"),
    ("Q132544044", "Ariel Motor Company"),
    ("Q63049904", "Aston Martin"),
    ("Q758549", "Auburn"),
    ("Q23317", "Audi"),
    ("Q781156", "Austin Motor Company"),
    ("Q202944", "Austin-Healey"),
    ("Q27152", "Auto Union"),
    ("Q27110", "Autobianchi"),
    ("Q59186515", "Automobili Pininfarina"),
    ("Q789333", "Autozam"),
    ("Q2309", "AvtoVAZ"),
    ("Q27224", "Bentley"),
    ("Q1425751", "Bizzarrini"),
    ("Q26678", "BMW"),
    ("Q27377", "Borgward"),
    ("Q321785", "Brabus"),
    ("Q27392", "Bristol Cars"),
    ("Q1002267", "Bugatti (Automobili, EB110 era)"),
    ("Q27401", "Bugatti (original, Molsheim)"),
    ("Q2308012", "Bugatti (Automobiles, VW/Rimac era)"),
    ("Q27415", "Buick"),
    ("Q27423", "BYD Auto"),
    ("Q27436", "Cadillac"),
    ("Q838832", "Caterham Cars"),
    ("Q209374", "Checker Motors Corporation"),
    ("Q591001", "Chery"),
    ("Q29570", "Chevrolet"),
    ("Q29610", "Chrysler"),
    ("Q1093218", "Cisitalia"),
    ("Q6746", "Citroen"),
    ("Q31836845", "Cord"),
    ("Q1141056", "Crosley"),
    ("Q8352675", "Cupra"),
    ("Q99670567", "Czinger"),
    ("Q27460", "Dacia"),
    ("Q27483", "Daewoo Motors"),
    ("Q27511", "Daihatsu"),
    ("Q27539", "Daimler Company"),
    ("Q27543", "Datsun"),
    ("Q173085", "De Tomaso"),
    ("Q27550", "Delage"),
    ("Q783891", "Delahaye"),
    ("Q1241608", "DeLorean Motor Company"),
    ("Q27558", "DeSoto"),
    ("Q639268", "DKW"),
    ("Q27564", "Dodge"),
    ("Q27571", "Donkervoort"),
    ("Q16040593", "DS Automobiles"),
    ("Q27582", "Duesenberg"),
    ("Q1203417", "Eagle"),
    ("Q1287696", "Edsel"),
    ("Q382940", "Eunos"),
    ("Q743344", "Facel Vega"),
    ("Q27586", "Ferrari"),
    ("Q27597", "Fiat"),
    ("Q1420893", "Fisker"),
    ("Q44294", "Ford"),
    ("Q27786", "FSO"),
    ("Q28616", "GAZ"),
    ("Q739000", "Geely"),
    ("Q21451523", "Genesis Motor"),
    ("Q1502806", "Geo"),
    ("Q167016", "Ginetta Cars"),
    ("Q664324", "Glas"),
    ("Q28993", "GMC"),
    ("Q65129456", "Gordon Murray Automotive"),
    ("Q1117001", "Great Wall Motor"),
    ("Q688366", "Gumpert"),
    ("Q1555170", "Gurgel"),
    ("Q28223947", "Haval"),
    ("Q1129749", "Healey"),
    ("Q13635788", "Hennessey Special Vehicles"),
    ("Q1544816", "Hillman"),
    ("Q1422710", "Hindustan Motors"),
    ("Q867667", "Hino Motors"),
    ("Q29068", "Hispano-Suiza"),
    ("Q29281", "Holden"),
    ("Q1624372", "Holden Special Vehicles"),
    ("Q9584", "Honda"),
    ("Q1611731", "Hongqi"),
    ("Q29296", "Horch"),
    ("Q1313428", "Hudson"),
    ("Q1636194", "Humber"),
    ("Q213487", "Hummer"),
    ("Q55931", "Hyundai"),
    ("Q29666", "IFA"),
    ("Q668822", "Imperial"),
    ("Q29714", "Infiniti"),
    ("Q1664128", "Innocenti"),
    ("Q704981", "Isdera"),
    ("Q426892", "Iso Rivolta"),
    ("Q29803", "Isuzu"),
    ("Q283754", "Italdesign Giugiaro"),
    ("Q30055", "Jaguar Cars"),
    ("Q30113", "Jeep"),
    ("Q30348", "Jensen Motors"),
    ("Q1339929", "Kaiser Motors"),
    ("Q35349", "Kia"),
    ("Q35594", "Koenigsegg"),
    ("Q35676", "Lada"),
    ("Q35879", "Lagonda"),
    ("Q35886", "Lamborghini"),
    ("Q35896", "Lancia"),
    ("Q26777551", "Land Rover"),
    ("Q390114", "LaSalle"),
    ("Q35919", "Lexus"),
    ("Q98139925", "Li Auto"),
    ("Q216796", "Lincoln"),
    ("Q35935", "Lotus Cars"),
    ("Q28027517", "Lucid Motors"),
    ("Q27555283", "Lynk & Co"),
    ("Q848059", "Mahindra & Mahindra"),
    ("Q1637323", "Marcos Engineering"),
    ("Q963718", "Maruti Suzuki"),
    ("Q35962", "Maserati"),
    ("Q35982", "Matra"),
    ("Q35989", "Maybach"),
    ("Q35996", "Mazda"),
    ("Q1351854", "McLaren Automotive"),
    ("Q458959", "Melkus"),
    ("Q36008", "Mercedes-Benz"),
    ("Q613883", "Mercury"),
    ("Q36018", "MG Cars"),
    ("Q1881443", "MG Motor"),
    ("Q116232", "Mini"),
    ("Q36033", "Mitsubishi Motors"),
    ("Q1544189", "Mitsuoka"),
    ("Q1165625", "Morgan Motor Company"),
    ("Q776997", "Morris Motors"),
    ("Q36044", "Moskvitch"),
    ("Q1081459", "Nash"),
    ("Q29921278", "NIO"),
    ("Q20165", "Nissan"),
    ("Q1542124", "Noble Automotive"),
    ("Q39898", "NSU Motorenwerke"),
    ("Q204327", "Oldsmobile"),
    ("Q40966", "Opel"),
    ("Q936574", "OSCA"),
    ("Q40971", "Packard"),
    ("Q749239", "Pagani"),
    ("Q731471", "Panhard"),
    ("Q746256", "Panoz"),
    ("Q1477881", "Pegaso"),
    ("Q6742", "Peugeot"),
    ("Q40978", "Pierce-Arrow"),
    ("Q1124857", "Plymouth"),
    ("Q4047174", "Polestar"),
    ("Q618466", "Polski Fiat"),
    ("Q40990", "Pontiac"),
    ("Q40993", "Porsche"),
    ("Q40996", "Praga"),
    ("Q268808", "Premier Automobiles Limited"),
    ("Q2110378", "Prince Motor Company"),
    ("Q1760318", "Puma Automoveis"),
    ("Q165708", "Ram Trucks"),
    ("Q2034074", "Rambler"),
    ("Q644813", "Reliant Motors"),
    ("Q6686", "Renault"),
    ("Q747594", "Riley Motor"),
    ("Q7334368", "Rimac Automobili"),
    ("Q7338847", "Rivian"),
    ("Q234803", "Rolls-Royce Motor Cars"),
    ("Q848620", "Rover Company"),
    ("Q559608", "Ruf Automobile"),
    ("Q1361906", "Saleen"),
    ("Q569235", "Saturn"),
    ("Q503487", "Scion"),
    ("Q188217", "SEAT"),
    ("Q173357", "Simca"),
    ("Q2001596", "Singer Motors"),
    ("Q55633247", "Singer Vehicle Design"),
    ("Q29637", "Skoda"),
    ("Q156490", "Smart"),
    ("Q1128836", "Spyker Cars"),
    ("Q221869", "SsangYong Motor"),
    ("Q1514963", "SSC North America"),
    ("Q938876", "Studebaker"),
    ("Q1465524", "Stutz"),
    ("Q172741", "Subaru"),
    ("Q2165627", "Sunbeam Motor Car Company"),
    ("Q181642", "Suzuki"),
    ("Q173151", "Talbot"),
    ("Q188514", "Tata Motors"),
    ("Q134079386", "Tatra"),
    ("Q124981765", "Tesla"),
    ("Q2447041", "Toyopet"),
    ("Q53268", "Toyota"),
    ("Q1140388", "Triumph Motor Company"),
    ("Q1504331", "Troller"),
    ("Q18396211", "Tucker"),
    ("Q46937", "TVR"),
    ("Q658242", "UAZ"),
    ("Q59187", "Vauxhall Motors"),
    ("Q1969669", "Vector Motors"),
    ("Q137177484", "Venturi"),
    ("Q246", "Volkswagen"),
    ("Q215293", "Volvo Cars"),
    ("Q59183", "Wanderer"),
    ("Q59181", "Wartburg"),
    ("Q694506", "Wiesmann"),
    ("Q1537825", "Willys"),
    ("Q530099", "Wolseley Motors"),
    ("Q3817853", "Wuling Motors"),
    ("Q63035278", "XPeng"),
    ("Q15770", "Yugo"),
    ("Q140266", "Zagato"),
    ("Q71233", "ZAZ"),
    ("Q106621124", "Zeekr"),
    ("Q638156", "ZIL"),
)

# Marques with no usable Wikidata entity, documented so their absence from
# KNOWN_MARQUES reads as a decision rather than an oversight. Candidates for
# Tier 3 sourcing later.
NOT_IN_WIKIDATA: tuple[tuple[str, str], ...] = (
    ("Gunther Werks", "no Wikidata entity exists at all (2026-07-28)"),
    (
        "Trabant",
        "exists only as an automobile-model-series entity (Q153681); the maker is VEB Sachsenring",
    ),
)

_LANDED_SQL = text(
    """
    SELECT DISTINCT external_id
    FROM raw_scrape.raw_records
    WHERE external_id = ANY(:qids)
    """
)


def missing_marques(session: Session) -> list[tuple[str, str]]:
    """Fixture entries absent from the raw landing zone. Empty means covered."""
    qids = [q for q, _ in KNOWN_MARQUES]
    landed = {row[0] for row in session.execute(_LANDED_SQL, {"qids": qids})}
    return [(q, name) for q, name in KNOWN_MARQUES if q not in landed]
