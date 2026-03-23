"""Leaderboard — rank backtest runs by any metric.

Pure function. No writes, no model changes.
"""

from django.db.models import F


def build_leaderboard(queryset, sort_by='sharpe_ratio', top_n=10):
    """Rank backtest runs by a chosen metric.

    Args:
        queryset: U001BacktestResult queryset (pre-filtered if needed).
        sort_by: Field name on BacktestResultBase to sort by (descending).
        top_n: Maximum number of results to return.

    Returns:
        List of dicts with run metadata + key metrics.
    """
    # Validate sort field exists on the model
    model = queryset.model
    valid_fields = {f.name for f in model._meta.get_fields()}
    if sort_by not in valid_fields:
        raise ValueError(
            f"Invalid sort field '{sort_by}'. "
            f"Valid fields: {sorted(valid_fields)}"
        )

    # Sort descending, nulls last
    results = queryset.select_related('run').order_by(
        F(sort_by).desc(nulls_last=True)
    )[:top_n]

    rows = []
    for r in results:
        rows.append({
            'rank': len(rows) + 1,
            'run_id': r.run_id,
            'strategy_id': r.run.strategy_id,
            'strategy_version': r.run.strategy_version,
            'run_label': r.run.run_label,
            'sweep_id': getattr(r.run, 'sweep_id', ''),
            'started_at': r.run.started_at,
            'total_trades': r.total_trades,
            'win_rate': r.win_rate,
            'total_pnl': r.total_pnl,
            'sharpe_ratio': r.sharpe_ratio,
            'sortino_ratio': r.sortino_ratio,
            'max_drawdown_pct': r.max_drawdown_pct,
            'profit_factor': r.profit_factor,
            'sort_value': getattr(r, sort_by),
        })

    return rows
