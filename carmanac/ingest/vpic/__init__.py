"""NHTSA vPIC ingestion: the first source that reaches the actual cars.

vPIC (Vehicle Product Information Catalog) is NHTSA's VIN-decode database -
every make, model, and manufacturer selling vehicles in the US since 1981,
keyed by the WMI codes that legally identify who built a vehicle. Tier 1:
this is regulatory data, not crowd-sourced.

Charter relevance: vPIC's WMI/make evidence is what arbitrates the
`manufacturer` roles Wikidata asserted, authenticates quarantined companies
by corroboration, and opens the door to models/model-years - the car data
the project exists for.
"""
