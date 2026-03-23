"""Backtest metrics — pure functions, no DB access.

All inputs are lists of ClosedTrade dataclasses or Decimal values.
"""

import math
from decimal import Decimal


ZERO = Decimal('0')


def compute_metrics(trades):
    """Compute all aggregate metrics from a list of ClosedTrade objects.

    Returns a dict ready to populate BacktestResultBase fields.
    """
    if not trades:
        return {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_pnl': ZERO,
            'avg_pnl_per_trade': None,
            'max_win': None,
            'max_loss': None,
            'win_rate': None,
            'profit_factor': None,
            'sharpe_ratio': None,
            'sortino_ratio': None,
            'max_drawdown_pct': None,
            'avg_hold_minutes': None,
            'entities_traded': 0,
            'entities_profitable': 0,
            'extra_metrics': {},
            'pnl_distribution': {},
        }

    pnls = [t.pnl for t in trades]
    total_pnl = sum(pnls)
    winners = [p for p in pnls if p > ZERO]
    losers = [p for p in pnls if p < ZERO]

    win_rate = Decimal(str(len(winners))) / Decimal(str(len(pnls))) if pnls else None

    # Profit factor: gross profits / gross losses
    gross_profit = sum(winners) if winners else ZERO
    gross_loss = abs(sum(losers)) if losers else ZERO
    profit_factor = (gross_profit / gross_loss) if gross_loss > ZERO else None

    # Per-entity stats
    entity_pnl = {}
    for t in trades:
        entity_pnl.setdefault(t.asset_id, ZERO)
        entity_pnl[t.asset_id] += t.pnl
    entities_traded = len(entity_pnl)
    entities_profitable = sum(1 for v in entity_pnl.values() if v > ZERO)

    hold_minutes = [t.hold_minutes for t in trades]
    avg_hold = sum(hold_minutes) / len(hold_minutes) if hold_minutes else None

    return {
        'total_trades': len(trades),
        'winning_trades': len(winners),
        'losing_trades': len(losers),
        'total_pnl': total_pnl,
        'avg_pnl_per_trade': total_pnl / len(trades),
        'max_win': max(pnls) if pnls else None,
        'max_loss': min(pnls) if pnls else None,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'sharpe_ratio': compute_sharpe(pnls),
        'sortino_ratio': compute_sortino(pnls),
        'max_drawdown_pct': compute_max_drawdown(pnls),
        'avg_hold_minutes': avg_hold,
        'entities_traded': entities_traded,
        'entities_profitable': entities_profitable,
        'extra_metrics': {},
        'pnl_distribution': _build_pnl_distribution(pnls),
    }


def compute_sharpe(pnls, risk_free=ZERO):
    """Annualized Sharpe ratio from a list of per-trade PnL values.

    Uses per-trade returns (not time-series). Returns None if < 2 trades
    or zero standard deviation.
    """
    if len(pnls) < 2:
        return None

    floats = [float(p) for p in pnls]
    mean = sum(floats) / len(floats)
    rf = float(risk_free)
    variance = sum((x - mean) ** 2 for x in floats) / (len(floats) - 1)
    std = math.sqrt(variance)

    if std == 0:
        return None

    return Decimal(str((mean - rf) / std)).quantize(Decimal('0.000001'))


def compute_sortino(pnls, risk_free=ZERO):
    """Sortino ratio — like Sharpe but only penalizes downside volatility.

    Returns None if < 2 trades or zero downside deviation.
    """
    if len(pnls) < 2:
        return None

    floats = [float(p) for p in pnls]
    mean = sum(floats) / len(floats)
    rf = float(risk_free)

    downside = [min(x - rf, 0) ** 2 for x in floats]
    downside_var = sum(downside) / (len(floats) - 1)
    downside_std = math.sqrt(downside_var)

    if downside_std == 0:
        return None

    return Decimal(str((mean - rf) / downside_std)).quantize(Decimal('0.000001'))


def compute_max_drawdown(pnls):
    """Maximum drawdown as a percentage of peak cumulative PnL.

    Returns None if no trades or no drawdown occurred.
    Drawdown is expressed as a negative percentage (e.g. -25.5).
    """
    if not pnls:
        return None

    cumulative = ZERO
    peak = ZERO
    max_dd = ZERO

    for pnl in pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        if peak > ZERO:
            dd = ((cumulative - peak) / peak) * 100
            if dd < max_dd:
                max_dd = dd

    if max_dd == ZERO:
        return None

    return max_dd


def _build_pnl_distribution(pnls):
    """Build a simple histogram of PnL values for storage."""
    if not pnls:
        return {}
    floats = [float(p) for p in pnls]
    return {
        'min': min(floats),
        'max': max(floats),
        'median': sorted(floats)[len(floats) // 2],
        'count': len(floats),
    }
