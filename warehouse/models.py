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
    UNIVERSE_TYPE = None
    OBSERVATION_WINDOW_START = None
    OBSERVATION_WINDOW_END = None
    EXCLUSION_CRITERIA = None
    VERSION = None

    anchor_event = models.DateTimeField(null=True, blank=True)
    membership_end = models.DateTimeField(null=True, blank=True)

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

    timestamp = models.DateTimeField()

    objects = FeatureLayerQuerySet.as_manager()

    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=['timestamp']),
        ]


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

    timestamp = models.DateTimeField()

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

    OBSERVATION_WINDOW_START = timedelta(0)
    OBSERVATION_WINDOW_END = timedelta(minutes=5000)

    class Meta:
        unique_together = [('coin', 'timestamp')]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(high_price__gte=models.F('low_price')),
                name='ohlcv_high_gte_low',
            ),
            models.CheckConstraint(
                condition=models.Q(volume__gte=Decimal('0')),
                name='ohlcv_volume_non_negative',
            ),
        ]

    def clean(self):
        super().clean()
        if self.coin_id and self.timestamp:
            try:
                coin_obj = MigratedCoin.objects.get(
                    mint_address=self.coin_id,
                )
            except MigratedCoin.DoesNotExist:
                return
            if coin_obj.anchor_event:
                window_start = (
                    coin_obj.anchor_event + self.OBSERVATION_WINDOW_START
                )
                window_end = (
                    coin_obj.anchor_event + self.OBSERVATION_WINDOW_END
                )
                if not (window_start <= self.timestamp <= window_end):
                    raise ValidationError(
                        f"Timestamp {self.timestamp} is outside the "
                        f"observation window "
                        f"[{window_start}, {window_end}]"
                    )

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
        "interval even when no holder change occurred."
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
    ACCESS_PATTERN = "Get all trades for asset X between T1 and T2"
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
