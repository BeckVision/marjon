from datetime import datetime, timedelta, timezone as dt_timezone
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

    # -- Window properties (dispatch on UNIVERSE_TYPE) --

    @property
    def window_start_time(self):
        """Absolute datetime when this asset's observation window opens."""
        if self.UNIVERSE_TYPE == 'event-driven':
            if self.anchor_event is None:
                return None
            return self.anchor_event + self.OBSERVATION_WINDOW_START
        elif self.UNIVERSE_TYPE == 'calendar-driven':
            return self.OBSERVATION_WINDOW_START  # absolute datetime or None
        return None

    @property
    def window_end_time(self):
        """Absolute datetime when this asset's observation window closes."""
        if self.UNIVERSE_TYPE == 'event-driven':
            if self.anchor_event is None:
                return None
            return self.anchor_event + self.OBSERVATION_WINDOW_END
        elif self.UNIVERSE_TYPE == 'calendar-driven':
            return self.OBSERVATION_WINDOW_END  # absolute datetime or None
        return None

    @property
    def is_mature(self):
        """True if the observation window has closed (current time past window end)."""
        from django.utils import timezone
        we = self.window_end_time
        if we is None:
            return False  # unbounded or missing anchor — not mature
        return timezone.now() >= we


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
        """Validate timestamp is within the asset's observation window (DQ-005).

        Uses the asset's window_start_time/window_end_time properties which
        dispatch on UNIVERSE_TYPE — works for both event-driven and calendar-driven.
        """
        super().clean()
        if not self.timestamp:
            return
        # Dynamically discover the FK to the universe model — no hardcoded field names
        fk_field = None
        for field in self.__class__._meta.get_fields():
            if isinstance(field, models.ForeignKey) and issubclass(field.related_model, UniverseBase):
                fk_field = field
                break
        if fk_field is None:
            return
        fk_value = getattr(self, fk_field.attname, None)
        if not fk_value:
            return
        try:
            asset = fk_field.related_model.objects.get(
                **{fk_field.remote_field.field_name: fk_value}
            )
        except fk_field.related_model.DoesNotExist:
            return
        ws = asset.window_start_time
        we = asset.window_end_time
        if ws is not None and we is not None:
            if not (ws <= self.timestamp <= we):
                raise ValidationError(
                    f"Timestamp {self.timestamp} is outside the "
                    f"observation window [{ws}, {we}]"
                )
        elif ws is not None:
            if self.timestamp < ws:
                raise ValidationError(
                    f"Timestamp {self.timestamp} is before "
                    f"observation window start {ws}"
                )
        elif we is not None:
            if self.timestamp > we:
                raise ValidationError(
                    f"Timestamp {self.timestamp} is after "
                    f"observation window end {we}"
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
# Enums — operational infrastructure
# ---------------------------------------------------------------------------

class RunStatus(models.TextChoices):
    STARTED = 'started', 'Started'
    COMPLETE = 'complete', 'Complete'
    ERROR = 'error', 'Error'


class PipelineCompleteness(models.TextChoices):
    NOT_STARTED = 'not_started', 'Not Started'
    IN_PROGRESS = 'in_progress', 'In Progress'
    PARTIAL = 'partial', 'Partial'
    WINDOW_COMPLETE = 'window_complete', 'Window Complete'
    ERROR = 'error', 'Error'


class RunMode(models.TextChoices):
    BOOTSTRAP = 'bootstrap', 'Bootstrap'
    STEADY_STATE = 'steady_state', 'Steady State'
    REFILL = 'refill', 'Re-fill'


class TradeType(models.TextChoices):
    BUY = 'BUY', 'Buy'
    SELL = 'SELL', 'Sell'


class SkipReason(models.TextChoices):
    NO_TRADE_EVENT = 'no_trade_event', 'No Trade Event'
    FAILED = 'failed', 'Failed Transaction'
    PARSE_ERROR = 'parse_error', 'Parse Error'


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
    name = models.CharField(max_length=200, null=True, blank=True, help_text="Token name from Moralis")
    symbol = models.CharField(max_length=50, null=True, blank=True, help_text="Token symbol from Moralis")
    decimals = models.PositiveSmallIntegerField(null=True, blank=True, help_text="SPL token decimals (usually 6, but not always)")
    logo_url = models.URLField(max_length=500, null=True, blank=True, help_text="Token logo URL from Moralis")
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
    DATA_SOURCE = "GeckoTerminal"
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
    DATA_SOURCE = "Shyft, Helius"
    REFRESH_POLICY = "Daily"
    VERSION = "1.0"

    coin = models.ForeignKey(
        MigratedCoin,
        to_field='mint_address',
        on_delete=models.CASCADE,
        related_name='raw_transactions',
    )
    tx_signature = models.CharField(max_length=128)
    trade_type = models.CharField(max_length=4, choices=TradeType.choices)
    wallet_address = models.CharField(max_length=64)
    token_amount = models.BigIntegerField()
    sol_amount = models.BigIntegerField()
    pool_address = models.CharField(max_length=64)
    tx_fee = models.DecimalField(max_digits=38, decimal_places=18)
    lp_fee = models.BigIntegerField()
    protocol_fee = models.BigIntegerField()
    coin_creator_fee = models.BigIntegerField()
    pool_token_reserves = models.BigIntegerField(null=True, blank=True)
    pool_sol_reserves = models.BigIntegerField(null=True, blank=True)
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['coin', 'timestamp']),
            models.Index(fields=['tx_signature']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['coin', 'tx_signature'],
                name='rd001_unique_tx_per_coin',
            ),
            models.CheckConstraint(
                condition=models.Q(token_amount__gt=0),
                name='rd001_token_amount_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(sol_amount__gte=0),
                name='rd001_sol_amount_non_negative',
            ),
            models.CheckConstraint(
                condition=models.Q(trade_type__in=['BUY', 'SELL']),
                name='rd001_trade_type_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(pool_token_reserves__gte=0),
                name='rd001_pool_token_reserves_non_negative',
            ),
            models.CheckConstraint(
                condition=models.Q(pool_sol_reserves__gte=0),
                name='rd001_pool_sol_reserves_non_negative',
            ),
            models.CheckConstraint(
                condition=models.Q(lp_fee__gte=0),
                name='rd001_lp_fee_non_negative',
            ),
            models.CheckConstraint(
                condition=models.Q(protocol_fee__gte=0),
                name='rd001_protocol_fee_non_negative',
            ),
            models.CheckConstraint(
                condition=models.Q(coin_creator_fee__gte=0),
                name='rd001_coin_creator_fee_non_negative',
            ),
        ]

    def __str__(self):
        return f"{self.coin_id} {self.tx_signature[:12]}… @ {self.timestamp}"


class SkippedTransaction(models.Model):
    """
    Not a paradigm table — operational infrastructure for storing
    unparsed/skipped transactions from the Shyft API.
    """
    tx_signature = models.CharField(max_length=128)
    timestamp = models.DateTimeField()
    coin = models.ForeignKey(
        MigratedCoin,
        to_field='mint_address',
        on_delete=models.CASCADE,
        related_name='skipped_transactions',
    )
    pool_address = models.CharField(max_length=64)
    tx_type = models.CharField(max_length=64)
    tx_status = models.CharField(max_length=32)
    skip_reason = models.CharField(max_length=32, choices=SkipReason.choices)
    raw_json = models.JSONField()
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['coin', 'tx_signature'],
                name='rd001_skipped_unique_tx_per_coin',
            ),
        ]

    def __str__(self):
        return f"{self.coin_id} {self.tx_signature[:12]}… skipped ({self.skip_reason})"


# ---------------------------------------------------------------------------
# Operational models — pipeline tracking (PDP8)
# ---------------------------------------------------------------------------

class PipelineBatchRun(models.Model):
    """
    Tracks a single pipeline invocation (scheduled or manual).
    One batch may process many coins (FL-001/FL-002) or discover many tokens (universe).
    Not a paradigm model — operational infrastructure for pipeline tracking.
    """
    pipeline_id = models.CharField(max_length=20)  # "universe", "fl001", "fl002"
    mode = models.CharField(max_length=20, choices=RunMode.choices)
    status = models.CharField(max_length=20, choices=RunStatus.choices)

    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    coins_attempted = models.IntegerField(default=0)
    coins_succeeded = models.IntegerField(default=0)
    coins_failed = models.IntegerField(default=0)

    cu_consumed = models.IntegerField(default=0)
    api_calls = models.IntegerField(default=0)

    error_message = models.TextField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['-started_at'], name='idx_batch_started'),
            models.Index(
                fields=['pipeline_id', '-started_at'],
                name='idx_batch_pipeline_started',
            ),
        ]

    def __str__(self):
        return (
            f"{self.pipeline_id} {self.mode} {self.status} "
            f"({self.started_at:%Y-%m-%d %H:%M})"
        )


class PipelineRunBase(models.Model):
    """
    Abstract base for pipeline run tracking.
    Paradigm-level: defines what a pipeline run records.
    Concrete models add a FK to their specific universe model.

    Not a quantitative trading paradigm model (not UniverseBase/FeatureLayerBase).
    Operational infrastructure that follows the same abstract base pattern.
    """
    batch = models.ForeignKey(
        PipelineBatchRun, on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='%(class)s_runs',
        help_text="Parent batch. Null for manual one-off runs.",
    )
    layer_id = models.CharField(max_length=20)
    mode = models.CharField(max_length=20, choices=RunMode.choices)
    status = models.CharField(max_length=20, choices=RunStatus.choices)

    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    records_loaded = models.IntegerField(default=0)
    records_expected = models.IntegerField(null=True, blank=True)
    time_range_start = models.DateTimeField(null=True, blank=True)
    time_range_end = models.DateTimeField(null=True, blank=True)

    error_message = models.TextField(null=True, blank=True)

    cu_consumed = models.IntegerField(default=0)
    api_calls = models.IntegerField(default=0)

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.layer_id} {self.status} ({self.started_at:%Y-%m-%d %H:%M})"


class U001PipelineRun(PipelineRunBase):
    """
    Pipeline run tracking for U-001 (Graduated Pump.fun Tokens).
    Adds FK to MigratedCoin.
    """
    coin = models.ForeignKey(
        MigratedCoin, to_field='mint_address',
        on_delete=models.CASCADE,
        related_name='pipeline_runs',
    )

    class Meta:
        indexes = [
            models.Index(
                fields=['coin', 'layer_id', '-started_at'],
                name='idx_u001run_coin_layer',
            ),
            models.Index(fields=['-started_at'], name='idx_u001run_started'),
            models.Index(fields=['status'], name='idx_u001run_status'),
        ]

    def __str__(self):
        return (
            f"{self.coin_id} {self.layer_id} {self.status} "
            f"({self.started_at:%Y-%m-%d %H:%M})"
        )


# ---------------------------------------------------------------------------
# Operational models — pipeline status cache
# ---------------------------------------------------------------------------

class PipelineStatusBase(models.Model):
    """
    Abstract base for pipeline status cache.
    One row per entity per layer — updated in place, never appended.
    Not a paradigm model — operational infrastructure.
    Concrete models add FK to their specific universe model and to their concrete PipelineRun.
    """
    layer_id = models.CharField(max_length=20)
    status = models.CharField(
        max_length=20, choices=PipelineCompleteness.choices,
        default=PipelineCompleteness.NOT_STARTED,
    )
    watermark = models.DateTimeField(
        null=True, blank=True,
        help_text="Cached watermark — latest timestamp in the feature layer table for this entity. "
                  "Source of truth is the feature layer table itself. Updated after each successful run.",
    )
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(
        null=True, blank=True,
        help_text="Error message from the last failed run. Null if last run succeeded.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.layer_id} {self.status}"


class U001PipelineStatus(PipelineStatusBase):
    """
    Pipeline status cache for U-001 (Graduated Pump.fun Tokens).
    One row per coin per layer. Updated in place after each pipeline run.
    """
    coin = models.ForeignKey(
        MigratedCoin, to_field='mint_address',
        on_delete=models.CASCADE,
        related_name='pipeline_statuses',
    )
    last_run = models.ForeignKey(
        U001PipelineRun,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='+',
    )

    class Meta:
        unique_together = [('coin', 'layer_id')]
        indexes = [
            models.Index(fields=['status'], name='idx_u001status_status'),
            models.Index(fields=['layer_id', 'status'], name='idx_u001status_layer_status'),
        ]

    def __str__(self):
        return f"{self.coin_id} {self.layer_id} {self.status}"


# ---------------------------------------------------------------------------
# Concrete models — U-002 (Major Crypto Assets)
# ---------------------------------------------------------------------------

class BinanceAsset(UniverseBase):
    UNIVERSE_ID = "U-002"
    NAME = "Major Crypto Assets"
    INCLUSION_CRITERIA = "Fixed list: BTCUSDT, ETHUSDT, SOLUSDT"
    UNIVERSE_TYPE = "calendar-driven"
    OBSERVATION_WINDOW_START = datetime(2024, 3, 1, tzinfo=dt_timezone.utc)
    OBSERVATION_WINDOW_END = None    # open-ended (perpetual)
    EXCLUSION_CRITERIA = None
    VERSION = "1.0"

    symbol = models.CharField(max_length=20, unique=True)
    base_asset = models.CharField(max_length=10)
    quote_asset = models.CharField(max_length=10)
    ingested_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.symbol


class U002OHLCVCandle(FeatureLayerBase):
    """OHLCV+ spot klines from Binance (1-minute candles)."""
    LAYER_ID = "U002-FL-001"
    UNIVERSE_ID = "U-002"
    NAME = "OHLCV+ (Spot Klines)"
    TEMPORAL_RESOLUTION = timedelta(minutes=1)
    AVAILABILITY_RULE = "end-of-interval"
    GAP_HANDLING = "No candle if exchange returns no data for that interval"
    DATA_SOURCE = "Binance"
    REFRESH_POLICY = "Daily"
    VERSION = "1.0"

    asset = models.ForeignKey(
        BinanceAsset,
        to_field='symbol',
        on_delete=models.CASCADE,
        related_name='ohlcv_candles',
    )
    open_price = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    high_price = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    low_price = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    close_price = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    volume = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    quote_volume = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    trade_count = models.IntegerField(null=True)
    taker_buy_volume = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    taker_buy_quote_volume = models.DecimalField(max_digits=20, decimal_places=8, null=True)

    class Meta:
        unique_together = [('asset', 'timestamp')]
        indexes = [
            models.Index(fields=['timestamp']),
            models.Index(
                fields=['asset', '-timestamp'],
                name='idx_u002ohlcv_asset_ts',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(high_price__gte=models.F('low_price')),
                name='u002_ohlcv_high_gte_low',
            ),
            models.CheckConstraint(
                condition=models.Q(volume__gte=0),
                name='u002_ohlcv_volume_non_neg',
            ),
            models.CheckConstraint(
                condition=models.Q(quote_volume__gte=0),
                name='u002_ohlcv_quote_vol_non_neg',
            ),
            models.CheckConstraint(
                condition=models.Q(taker_buy_volume__lte=models.F('volume')),
                name='u002_ohlcv_taker_lte_vol',
            ),
        ]


class U002FuturesMetrics(FeatureLayerBase):
    """Open interest + long/short ratios from Binance futures (5-minute)."""
    LAYER_ID = "U002-FL-003"
    UNIVERSE_ID = "U-002"
    NAME = "Futures Metrics"
    TEMPORAL_RESOLUTION = timedelta(minutes=5)
    AVAILABILITY_RULE = "publication-time"
    GAP_HANDLING = "No row if data missing for interval"
    DATA_SOURCE = "Binance"
    REFRESH_POLICY = "Daily (next-day CSV)"
    VERSION = "1.0"

    asset = models.ForeignKey(
        BinanceAsset,
        to_field='symbol',
        on_delete=models.CASCADE,
        related_name='futures_metrics',
    )
    sum_open_interest = models.DecimalField(
        max_digits=20, decimal_places=10, null=True,
        help_text="Total OI in base asset",
    )
    sum_open_interest_value = models.DecimalField(
        max_digits=24, decimal_places=10, null=True,
        help_text="Total OI in USDT",
    )
    count_toptrader_long_short_ratio = models.DecimalField(
        max_digits=12, decimal_places=8, null=True,
    )
    sum_toptrader_long_short_ratio = models.DecimalField(
        max_digits=12, decimal_places=8, null=True,
    )
    count_long_short_ratio = models.DecimalField(
        max_digits=12, decimal_places=8, null=True,
    )
    sum_taker_long_short_vol_ratio = models.DecimalField(
        max_digits=12, decimal_places=8, null=True,
    )

    class Meta:
        unique_together = [('asset', 'timestamp')]
        indexes = [
            models.Index(fields=['timestamp']),
            models.Index(
                fields=['asset', '-timestamp'],
                name='idx_u002metrics_asset_ts',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(sum_open_interest__gte=0),
                name='u002_metrics_oi_non_neg',
            ),
            models.CheckConstraint(
                condition=models.Q(sum_open_interest_value__gte=0),
                name='u002_metrics_oi_val_non_neg',
            ),
        ]


class U002FundingRate(FeatureLayerBase):
    """Perpetual futures funding rate from Binance (every 8 hours)."""
    LAYER_ID = "U002-FL-004"
    UNIVERSE_ID = "U-002"
    NAME = "Funding Rate"
    TEMPORAL_RESOLUTION = timedelta(hours=8)
    AVAILABILITY_RULE = "publication-time"
    GAP_HANDLING = "Missing funding events indicate exchange issues"
    DATA_SOURCE = "Binance"
    REFRESH_POLICY = "Daily (monthly CSV)"
    VERSION = "1.0"

    asset = models.ForeignKey(
        BinanceAsset,
        to_field='symbol',
        on_delete=models.CASCADE,
        related_name='funding_rates',
    )
    funding_interval_hours = models.IntegerField(null=True)
    last_funding_rate = models.DecimalField(
        max_digits=12, decimal_places=10, null=True,
    )

    class Meta:
        unique_together = [('asset', 'timestamp')]
        indexes = [
            models.Index(fields=['timestamp']),
            models.Index(
                fields=['asset', '-timestamp'],
                name='idx_u002funding_asset_ts',
            ),
        ]


# ---------------------------------------------------------------------------
# Operational models — U-002 pipeline tracking
# ---------------------------------------------------------------------------

class U002PipelineRun(PipelineRunBase):
    """Pipeline run tracking for U-002 (Major Crypto Assets)."""
    asset = models.ForeignKey(
        BinanceAsset, to_field='symbol',
        on_delete=models.CASCADE,
        related_name='pipeline_runs',
    )

    class Meta:
        indexes = [
            models.Index(
                fields=['asset', 'layer_id', '-started_at'],
                name='idx_u002run_asset_layer',
            ),
            models.Index(fields=['-started_at'], name='idx_u002run_started'),
            models.Index(fields=['status'], name='idx_u002run_status'),
        ]

    def __str__(self):
        return (
            f"{self.asset_id} {self.layer_id} {self.status} "
            f"({self.started_at:%Y-%m-%d %H:%M})"
        )


class U002PipelineStatus(PipelineStatusBase):
    """Pipeline status cache for U-002 (Major Crypto Assets)."""
    asset = models.ForeignKey(
        BinanceAsset, to_field='symbol',
        on_delete=models.CASCADE,
        related_name='pipeline_statuses',
    )
    last_run = models.ForeignKey(
        U002PipelineRun,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='+',
    )

    class Meta:
        unique_together = [('asset', 'layer_id')]
        indexes = [
            models.Index(fields=['status'], name='idx_u002status_status'),
            models.Index(
                fields=['layer_id', 'status'],
                name='idx_u002status_layer_status',
            ),
        ]

    def __str__(self):
        return f"{self.asset_id} {self.layer_id} {self.status}"
