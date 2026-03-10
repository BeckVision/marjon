from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from .managers import (
    FeatureLayerQuerySet,
    ReferenceTableQuerySet,
    UniverseQuerySet,
)


# ---------------------------------------------------------------------------
# Abstract bases
# ---------------------------------------------------------------------------

class UniverseBase(models.Model):
    UNIVERSE_ID = None
    NAME = None
    INCLUSION_CRITERIA = None
    UNIVERSE_TYPE = None            # "event-driven" or "calendar-driven"
    OBSERVATION_WINDOW_START = None  # offset from anchor (event-driven) or absolute time
    OBSERVATION_WINDOW_END = None    # same; None = unbounded
    EXCLUSION_CRITERIA = None
    VERSION = None

    anchor_event = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The reference point (T0) for this asset. "
                  "Populated for event-driven universes. "
                  "Null for calendar-driven universes.",
    )
    membership_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this asset left the universe. "
                  "Null = still a member.",
    )

    objects = UniverseQuerySet.as_manager()

    class Meta:
        abstract = True


class FeatureLayerBase(models.Model):
    LAYER_ID = None
    UNIVERSE_ID = None
    NAME = None
    TEMPORAL_RESOLUTION = None
    AVAILABILITY_RULE = None
    GAP_HANDLING = None
    DATA_SOURCE = None
    REFRESH_POLICY = None
    VERSION = None

    timestamp = models.DateTimeField(
        help_text="Observation timestamp, UTC. Whether this represents "
                  "interval start or end is defined by WDP9.",
    )

    objects = FeatureLayerQuerySet.as_manager()

    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=['timestamp']),
        ]

    def clean(self):
        """Validate timestamp is within the coin's observation window (DQ-005)."""
        super().clean()
        coin_id = getattr(self, 'coin_id', None)
        if not coin_id or not self.timestamp:
            return
        # Discover the universe model via the concrete class's 'coin' FK
        try:
            fk_field = self.__class__._meta.get_field('coin')
        except Exception:
            return
        UniverseModel = fk_field.related_model
        window_start = getattr(UniverseModel, 'OBSERVATION_WINDOW_START', None)
        window_end = getattr(UniverseModel, 'OBSERVATION_WINDOW_END', None)
        if window_start is None or window_end is None:
            return
        try:
            coin_obj = UniverseModel.objects.get(
                **{fk_field.remote_field.field_name: coin_id}
            )
        except UniverseModel.DoesNotExist:
            return
        if coin_obj.anchor_event:
            ws = coin_obj.anchor_event + window_start
            we = coin_obj.anchor_event + window_end
            if not (ws <= self.timestamp <= we):
                raise ValidationError(
                    f"Timestamp {self.timestamp} is outside the "
                    f"observation window [{ws}, {we}]"
                )


class ReferenceTableBase(models.Model):
    REFERENCE_ID = None
    UNIVERSE_ID = None
    NAME = None
    RECORD_TYPE = None
    AVAILABILITY_RULE = None
    ACCESS_PATTERN = None
    DATA_SOURCE = None
    REFRESH_POLICY = None
    VERSION = None

    timestamp = models.DateTimeField(
        help_text="Exact event time, UTC. PIT behavior depends on "
                  "the declared AVAILABILITY_RULE.",
    )

    objects = ReferenceTableQuerySet.as_manager()

    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=['timestamp']),
        ]


# ---------------------------------------------------------------------------
# Concrete models — U-001
# ---------------------------------------------------------------------------

class MigratedCoin(UniverseBase):
    UNIVERSE_ID = "U-001"
    NAME = "Graduated Pump.fun Tokens — Early Lifecycle"
    INCLUSION_CRITERIA = (
        "All tokens launched on pump.fun and migrated to Pumpswap"
    )
    UNIVERSE_TYPE = "event-driven"
    OBSERVATION_WINDOW_START = timedelta(0)
    OBSERVATION_WINDOW_END = timedelta(minutes=5000)
    EXCLUSION_CRITERIA = None
    VERSION = "1.0"

    mint_address = models.CharField(max_length=50, unique=True)
    ingested_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.mint_address


class OHLCVCandle(FeatureLayerBase):
    LAYER_ID = "FL-001"
    UNIVERSE_ID = "U-001"
    NAME = "OHLCV Price Data"
    TEMPORAL_RESOLUTION = timedelta(minutes=5)
    AVAILABILITY_RULE = "end-of-interval"
    GAP_HANDLING = "No candle created if no trades occurred in the interval"
    DATA_SOURCE = "DexPaprika / GeckoTerminal"
    REFRESH_POLICY = "Daily"
    VERSION = "1.0"

    coin = models.ForeignKey(
        MigratedCoin,
        to_field='mint_address',
        on_delete=models.CASCADE,
        related_name='ohlcv_candles',
    )
    open_price = models.DecimalField(
        max_digits=38, decimal_places=18, null=True,
    )
    high_price = models.DecimalField(
        max_digits=38, decimal_places=18, null=True,
    )
    low_price = models.DecimalField(
        max_digits=38, decimal_places=18, null=True,
    )
    close_price = models.DecimalField(
        max_digits=38, decimal_places=18, null=True,
    )
    volume = models.DecimalField(
        max_digits=38, decimal_places=18, null=True,
    )
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('coin', 'timestamp')]
        indexes = [
            models.Index(fields=['timestamp']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(high_price__gte=models.F('low_price')),
                name='ohlcv_high_gte_low',
            ),
            models.CheckConstraint(
                condition=models.Q(volume__gte=Decimal('0')),
                name='ohlcv_volume_non_negative',
            ),
            # DQ-003: open_price must be between low_price and high_price
            models.CheckConstraint(
                condition=(
                    models.Q(open_price__isnull=True)
                    | models.Q(low_price__isnull=True)
                    | models.Q(high_price__isnull=True)
                    | models.Q(
                        open_price__gte=models.F('low_price'),
                        open_price__lte=models.F('high_price'),
                    )
                ),
                name='ohlcv_open_in_range',
            ),
            # DQ-003: close_price must be between low_price and high_price
            models.CheckConstraint(
                condition=(
                    models.Q(close_price__isnull=True)
                    | models.Q(low_price__isnull=True)
                    | models.Q(high_price__isnull=True)
                    | models.Q(
                        close_price__gte=models.F('low_price'),
                        close_price__lte=models.F('high_price'),
                    )
                ),
                name='ohlcv_close_in_range',
            ),
        ]

    def __str__(self):
        return f"{self.coin_id} @ {self.timestamp}"


class HolderSnapshot(FeatureLayerBase):
    LAYER_ID = "FL-002"
    UNIVERSE_ID = "U-001"
    NAME = "Holder Snapshots"
    TEMPORAL_RESOLUTION = timedelta(minutes=5)
    AVAILABILITY_RULE = "end-of-interval"
    GAP_HANDLING = (
        "Every interval has a snapshot — Moralis returns data for every "
        "interval even when no holder change occurred. Dead coins show "
        "netHolderChange=0 with stable totalHolders. No gaps from source."
    )
    DATA_SOURCE = "Moralis API"
    REFRESH_POLICY = "Daily"
    VERSION = "1.0"

    coin = models.ForeignKey(
        MigratedCoin,
        to_field='mint_address',
        on_delete=models.CASCADE,
        related_name='holder_snapshots',
    )
    total_holders = models.BigIntegerField(null=True)
    net_holder_change = models.BigIntegerField(null=True)
    holder_percent_change = models.DecimalField(
        max_digits=20, decimal_places=10, null=True,
    )
    acquired_via_swap = models.BigIntegerField(null=True)
    acquired_via_transfer = models.BigIntegerField(null=True)
    acquired_via_airdrop = models.BigIntegerField(null=True)
    holders_in_whales = models.BigIntegerField(null=True)
    holders_in_sharks = models.BigIntegerField(null=True)
    holders_in_dolphins = models.BigIntegerField(null=True)
    holders_in_fish = models.BigIntegerField(null=True)
    holders_in_octopus = models.BigIntegerField(null=True)
    holders_in_crabs = models.BigIntegerField(null=True)
    holders_in_shrimps = models.BigIntegerField(null=True)
    holders_out_whales = models.BigIntegerField(null=True)
    holders_out_sharks = models.BigIntegerField(null=True)
    holders_out_dolphins = models.BigIntegerField(null=True)
    holders_out_fish = models.BigIntegerField(null=True)
    holders_out_octopus = models.BigIntegerField(null=True)
    holders_out_crabs = models.BigIntegerField(null=True)
    holders_out_shrimps = models.BigIntegerField(null=True)
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('coin', 'timestamp')]
        indexes = [
            models.Index(fields=['timestamp']),
        ]

    def __str__(self):
        return f"{self.coin_id} @ {self.timestamp}"


class PoolMapping(models.Model):
    coin = models.ForeignKey(
        MigratedCoin,
        to_field='mint_address',
        on_delete=models.CASCADE,
        related_name='pool_mappings',
    )
    pool_address = models.CharField(max_length=50)
    dex = models.CharField(max_length=50)
    source = models.CharField(max_length=50)
    created_at = models.DateTimeField(null=True, blank=True)
    discovered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('coin', 'pool_address')]

    def __str__(self):
        return f"{self.coin_id} -> {self.pool_address}"


class RawTransaction(ReferenceTableBase):
    REFERENCE_ID = "RD-001"
    UNIVERSE_ID = "U-001"
    NAME = "Raw Transaction Data"
    RECORD_TYPE = "Single trade (buy or sell)"
    AVAILABILITY_RULE = "event-time"
    ACCESS_PATTERN = "Get all trades for coin X between T1 and T2"
    DATA_SOURCE = "TBD"
    REFRESH_POLICY = "TBD"
    VERSION = "0.1"

    coin = models.ForeignKey(
        MigratedCoin,
        to_field='mint_address',
        on_delete=models.CASCADE,
        related_name='raw_transactions',
    )
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['timestamp']),
        ]

    def __str__(self):
        return f"{self.coin_id} @ {self.timestamp}"
