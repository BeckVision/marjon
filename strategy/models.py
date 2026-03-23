"""Strategy + backtest models — abstract bases and U-001 concretes.

Abstract bases use paradigm-level language ("entity", not "coin").
Concrete models add universe-specific FKs.
"""

from django.db import models

from warehouse.models import MigratedCoin


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class BacktestStatus(models.TextChoices):
    RUNNING = 'running', 'Running'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'


class ExitReason(models.TextChoices):
    TAKE_PROFIT = 'take_profit', 'Take Profit'
    STOP_LOSS = 'stop_loss', 'Stop Loss'
    TIMEOUT = 'timeout', 'Timeout'
    EXIT_SIGNAL = 'exit_signal', 'Exit Signal'
    FORCE_CLOSE = 'force_close', 'Force Close'


# ---------------------------------------------------------------------------
# Abstract bases
# ---------------------------------------------------------------------------

class BacktestRunBase(models.Model):
    """One row per backtest execution. Paradigm-level."""

    strategy_id = models.CharField(
        max_length=100,
        help_text="Strategy identifier, e.g. 'u001_volume_spike_v1'.",
    )
    strategy_version = models.IntegerField()
    run_label = models.CharField(
        max_length=200, blank=True, default='',
        help_text="Optional human-readable label for this run.",
    )
    data_start = models.DateTimeField(
        help_text="Start of the test window (inclusive).",
    )
    data_end = models.DateTimeField(
        help_text="End of the test window (inclusive).",
    )
    params_snapshot = models.JSONField(
        help_text="Frozen complete strategy config for reproducibility.",
    )
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=BacktestStatus.choices,
        default=BacktestStatus.RUNNING,
    )
    error_message = models.TextField(null=True, blank=True)
    sweep_id = models.CharField(
        max_length=100, blank=True, default='',
        db_index=True,
        help_text="Groups runs from the same parameter sweep.",
    )
    entities_tested = models.IntegerField(
        null=True, blank=True,
        help_text="Number of entities (assets) included in the backtest.",
    )

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.strategy_id} v{self.strategy_version} {self.status} ({self.started_at:%Y-%m-%d %H:%M})"


class BacktestResultBase(models.Model):
    """One-to-one scorecard for a backtest run. Paradigm-level."""

    # Trade counts
    total_trades = models.IntegerField(default=0)
    winning_trades = models.IntegerField(default=0)
    losing_trades = models.IntegerField(default=0)

    # PnL
    total_pnl = models.DecimalField(max_digits=38, decimal_places=18, default=0)
    avg_pnl_per_trade = models.DecimalField(
        max_digits=38, decimal_places=18, null=True, blank=True,
    )
    max_win = models.DecimalField(
        max_digits=38, decimal_places=18, null=True, blank=True,
    )
    max_loss = models.DecimalField(
        max_digits=38, decimal_places=18, null=True, blank=True,
    )

    # Ratios
    win_rate = models.DecimalField(
        max_digits=10, decimal_places=6, null=True, blank=True,
    )
    profit_factor = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True,
    )

    # Risk
    sharpe_ratio = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True,
    )
    sortino_ratio = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True,
    )
    max_drawdown_pct = models.DecimalField(
        max_digits=20, decimal_places=6, null=True, blank=True,
    )

    # Timing
    avg_hold_minutes = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True,
    )

    # Entity-level
    entities_traded = models.IntegerField(default=0)
    entities_profitable = models.IntegerField(default=0)

    # Extensible
    extra_metrics = models.JSONField(default=dict, blank=True)
    pnl_distribution = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True

    def __str__(self):
        return f"Result: {self.total_trades} trades, PnL={self.total_pnl}"


class BacktestTradeBase(models.Model):
    """Individual simulated trade. Paradigm-level."""

    entry_time = models.DateTimeField()
    exit_time = models.DateTimeField()
    entry_price = models.DecimalField(max_digits=38, decimal_places=18)
    exit_price = models.DecimalField(max_digits=38, decimal_places=18)
    entry_amount = models.DecimalField(max_digits=38, decimal_places=18)
    entry_reason = models.JSONField(
        help_text="Which signals fired with their values at entry.",
    )
    exit_reason = models.CharField(
        max_length=20, choices=ExitReason.choices,
    )
    pnl = models.DecimalField(max_digits=38, decimal_places=18)
    roi_pct = models.DecimalField(max_digits=20, decimal_places=6)
    hold_minutes = models.DecimalField(max_digits=20, decimal_places=2)

    class Meta:
        abstract = True

    def __str__(self):
        return f"Trade {self.entry_time} -> {self.exit_time} PnL={self.pnl}"


# ---------------------------------------------------------------------------
# Concrete models — U-001
# ---------------------------------------------------------------------------

class U001BacktestRun(BacktestRunBase):
    class Meta:
        indexes = [
            models.Index(
                fields=['strategy_id', '-started_at'],
                name='idx_u001bt_strategy_started',
            ),
            models.Index(fields=['-started_at'], name='idx_u001bt_started'),
        ]


class U001BacktestResult(BacktestResultBase):
    run = models.OneToOneField(
        U001BacktestRun, on_delete=models.CASCADE,
        related_name='result',
    )


class U001BacktestTrade(BacktestTradeBase):
    run = models.ForeignKey(
        U001BacktestRun, on_delete=models.CASCADE,
        related_name='trades',
    )
    coin = models.ForeignKey(
        MigratedCoin, to_field='mint_address',
        on_delete=models.CASCADE,
        related_name='backtest_trades',
    )

    class Meta:
        indexes = [
            models.Index(
                fields=['run', 'coin'],
                name='idx_u001bttrade_run_coin',
            ),
        ]
