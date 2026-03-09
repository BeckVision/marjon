"""
warehouse/models.py

Abstract base models encoding the quantitative trading paradigm.

The paradigm defines three table categories:
    - Universe     : master data, one row per asset
    - Feature layer: time-series facts, one row per asset per interval
    - Reference    : event facts, one row per discrete event

Each base encodes the paradigm-level attributes for its category.
Per-row data becomes Django model fields (database columns).
Per-definition constants are declared as None — concrete models override them.

What the bases do NOT contain:
    - Asset identity fields (vary by domain)
    - Foreign keys (vary by which universe model the concrete model belongs to)
    - Feature columns (vary by what each layer measures)
    - UniqueConstraints (require FK field, which isn't on the base)
    - CHECK constraints (dataset-specific, added in a later step)
    - .as_of() QuerySets (added in a later step)

Concrete models inherit a base and add all of the above.
"""

from django.db import models


# ==========================================================================
# Universe Base
#
# Quantitative trading paradigm definition:
#   "Which assets, what time scope."
#   One row per asset. Referenced by all feature layers and reference tables.
#
# Two universe types:
#   - Event-driven: each asset has its own anchor event (T0),
#     observation window is relative offsets from T0
#   - Calendar-driven: no per-asset anchor, observation window
#     is an absolute time range
#
# Observation window uses the event study convention (MacKinlay, 1997):
#   two offsets (t1, t2) for event-driven,
#   two absolute times for calendar-driven.
#
# Concrete models add:
#   - Asset identity field (varies by domain)
#   - Values for all per-definition constants
#   - Any additional master data fields
# ==========================================================================

class UniverseBase(models.Model):

    # --- Per-definition constants (quantitative trading paradigm attributes) ---
    # Override these in every concrete universe model.
    UNIVERSE_ID = None
    NAME = None
    INCLUSION_CRITERIA = None
    UNIVERSE_TYPE = None  # "event-driven" or "calendar-driven"
    OBSERVATION_WINDOW_START = None  # offset from anchor (event-driven) or absolute time (calendar-driven)
    OBSERVATION_WINDOW_END = None    # same; None = unbounded
    EXCLUSION_CRITERIA = None
    VERSION = None

    # --- Per-row fields (quantitative trading paradigm attributes) ---
    anchor_event = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The reference point (T0) for this asset. "
                  "Populated for event-driven universes (each asset has its own T0). "
                  "Null for calendar-driven universes (observation window is absolute).",
    )
    membership_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this asset left the universe. "
                  "Null = still a member (or universe has permanent membership). "
                  "Used for universes with rebalancing or natural exit events.",
    )

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.pk} (T0: {self.anchor_event})"


# ==========================================================================
# Feature Layer Base
#
# Quantitative trading paradigm definition:
#   "Time-aligned measurements of an asset within a universe,
#    at a fixed temporal resolution."
#   One observation per asset per time interval.
#
# Each concrete layer declares its own AVAILABILITY_RULE.
# The paradigm does not prescribe which PIT type a feature layer uses —
# that depends on the data source.
#
# Concrete models add:
#   - FK to the specific universe model
#   - Feature columns (the feature set)
#   - UniqueConstraint on (FK_field, timestamp)
#   - Values for all per-definition constants
# ==========================================================================

class FeatureLayerBase(models.Model):

    # --- Per-definition constants (quantitative trading paradigm attributes) ---
    # Override these in every concrete feature layer model.
    LAYER_ID = None
    UNIVERSE_ID = None
    NAME = None
    TEMPORAL_RESOLUTION = None  # representation depends on market (see dataset record)
    AVAILABILITY_RULE = None   # "end-of-interval", "event-time", or "publication-time"
    GAP_HANDLING = None
    DATA_SOURCE = None
    REFRESH_POLICY = None
    VERSION = None
    # Feature set: defined by each concrete model as additional fields.

    # --- Per-row field (quantitative trading paradigm attribute) ---
    timestamp = models.DateTimeField(
        help_text="Observation timestamp, UTC. Whether this represents "
                  "interval start or end is defined by the dataset's "
                  "timestamp convention (WDP9).",
    )

    class Meta:
        abstract = True

        indexes = [
            # Cross-sectional queries ("all assets at time T").
            # The per-asset time range index comes from the UniqueConstraint,
            # which concrete models define (because it includes the FK field).
            models.Index(
                fields=["timestamp"],
                name="%(app_label)s_%(class)s_ts_idx",
            ),
        ]

    def __str__(self):
        return f"{self.pk} @ {self.timestamp}"


# ==========================================================================
# Reference Table Base
#
# Quantitative trading paradigm definition:
#   "Granular event data outside the fixed time-interval grid,
#    queried on demand."
#   One row per discrete event.
#
# Each concrete table declares its own AVAILABILITY_RULE.
#
# Concrete models add:
#   - FK to the specific universe model
#   - Event identifier field (varies per table)
#   - Feature columns (the feature set)
#   - UniqueConstraint on (FK_field, timestamp, event_identifier)
#   - Values for all per-definition constants
# ==========================================================================

class ReferenceTableBase(models.Model):

    # --- Per-definition constants (quantitative trading paradigm attributes) ---
    # Override these in every concrete reference table model.
    REFERENCE_ID = None
    UNIVERSE_ID = None
    NAME = None
    RECORD_TYPE = None
    AVAILABILITY_RULE = None  # "end-of-interval", "event-time", or "publication-time"
    ACCESS_PATTERN = None
    DATA_SOURCE = None
    REFRESH_POLICY = None
    VERSION = None
    # Feature set: defined by each concrete model as additional fields.

    # --- Per-row field (quantitative trading paradigm attribute) ---
    timestamp = models.DateTimeField(
        help_text="Exact event time, UTC. PIT behavior depends on "
                  "the declared AVAILABILITY_RULE.",
    )

    class Meta:
        abstract = True

        indexes = [
            # Time-range queries ("all events for an asset between T1 and T2").
            models.Index(
                fields=["timestamp"],
                name="%(app_label)s_%(class)s_ts_idx",
            ),
        ]

    def __str__(self):
        return f"{self.pk} @ {self.timestamp}"
