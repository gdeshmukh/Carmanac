"""All ORM models.

Importing this package registers every table on `Base.metadata`. Alembic's
autogenerate compares that metadata against the live database, so a model that
is not reachable from here is invisible to migrations - and would be silently
dropped from the generated schema. Any new model must be imported here.
"""

from carmanac.db.base import Base
from carmanac.db.models.attributes import (
    ATTRIBUTE_DATA_TYPES,
    AttributeDefinition,
    ConfigurationAttribute,
)
from carmanac.db.models.hierarchy import Configuration, Generation, Make, Model, ModelYear
from carmanac.db.models.lookups import (
    BodyStyle,
    Drivetrain,
    FuelType,
    MarketRegion,
    Source,
    TransmissionType,
)
from carmanac.db.models.powertrain import (
    ConfigurationEngine,
    ConfigurationTransmission,
    Engine,
    Transmission,
)
from carmanac.db.models.provenance import ExternalId, FieldProvenance, RawRecord

__all__ = [
    "ATTRIBUTE_DATA_TYPES",
    "AttributeDefinition",
    "Base",
    "BodyStyle",
    "Configuration",
    "ConfigurationAttribute",
    "ConfigurationEngine",
    "ConfigurationTransmission",
    "Drivetrain",
    "Engine",
    "ExternalId",
    "FieldProvenance",
    "FuelType",
    "Generation",
    "Make",
    "MarketRegion",
    "Model",
    "ModelYear",
    "RawRecord",
    "Source",
    "Transmission",
    "TransmissionType",
]
