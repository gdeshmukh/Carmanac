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
from carmanac.db.models.derivations import VehicleDerivation
from carmanac.db.models.hierarchy import (
    CataloguePeriod,
    Company,
    CompanyRoleAssignment,
    Configuration,
    Generation,
    GenerationModelLink,
    Model,
    ModelLine,
    ModelLineMember,
)
from carmanac.db.models.lookups import (
    Aspiration,
    BodyStyle,
    CompanyRole,
    Country,
    Currency,
    DerivationType,
    Drivetrain,
    FuelType,
    MarketRegion,
    PeriodKind,
    Source,
    TransmissionType,
)
from carmanac.db.models.media import (
    MEDIA_KINDS,
    MEDIA_ROLES,
    MediaAsset,
    MediaAttachment,
)
from carmanac.db.models.powertrain import (
    ConfigurationEngine,
    ConfigurationTransmission,
    Engine,
    Transmission,
)
from carmanac.db.models.provenance import ExternalId, FieldProvenance, RawRecord
from carmanac.db.models.reconciliation import (
    FLAG_KINDS,
    FLAG_STATUSES,
    MatchDecision,
    ReconciledRecord,
    ReconciliationFlag,
    SlugAlias,
)

__all__ = [
    "ATTRIBUTE_DATA_TYPES",
    "FLAG_KINDS",
    "FLAG_STATUSES",
    "MEDIA_KINDS",
    "MEDIA_ROLES",
    "Aspiration",
    "AttributeDefinition",
    "Base",
    "BodyStyle",
    "CataloguePeriod",
    "Company",
    "CompanyRole",
    "CompanyRoleAssignment",
    "Configuration",
    "ConfigurationAttribute",
    "ConfigurationEngine",
    "ConfigurationTransmission",
    "Country",
    "Currency",
    "DerivationType",
    "Drivetrain",
    "Engine",
    "ExternalId",
    "FieldProvenance",
    "FuelType",
    "Generation",
    "GenerationModelLink",
    "MarketRegion",
    "MatchDecision",
    "MediaAsset",
    "MediaAttachment",
    "Model",
    "ModelLine",
    "ModelLineMember",
    "PeriodKind",
    "RawRecord",
    "ReconciledRecord",
    "ReconciliationFlag",
    "SlugAlias",
    "Source",
    "Transmission",
    "TransmissionType",
    "VehicleDerivation",
]
