"""Signal effectiveness analysis — per-signal stats and correlation.

Pure functions operating on trade data. No writes, no model changes.
"""

from collections import defaultdict
from decimal import Decimal
from itertools import combinations

ZERO = Decimal('0')


def analyze_signal_effectiveness(trades):
    """Per-signal analysis: when a signal fires, what's the average outcome?

    Args:
        trades: Iterable of objects with entry_reason (dict) and pnl (Decimal).
            Works with both ClosedTrade dataclasses and U001BacktestTrade model instances.

    Returns:
        Dict mapping signal_id -> {fire_count, total_pnl, avg_pnl, win_count, win_rate}.
    """
    stats = defaultdict(lambda: {
        'fire_count': 0,
        'total_pnl': ZERO,
        'win_count': 0,
    })

    for trade in trades:
        reason = trade.entry_reason
        if not isinstance(reason, dict):
            continue
        pnl = Decimal(str(trade.pnl))
        for signal_id, fired in reason.items():
            if fired:
                s = stats[signal_id]
                s['fire_count'] += 1
                s['total_pnl'] += pnl
                if pnl > ZERO:
                    s['win_count'] += 1

    result = {}
    for signal_id, s in sorted(stats.items()):
        count = s['fire_count']
        result[signal_id] = {
            'fire_count': count,
            'total_pnl': s['total_pnl'],
            'avg_pnl': s['total_pnl'] / count if count > 0 else ZERO,
            'win_count': s['win_count'],
            'win_rate': Decimal(str(s['win_count'])) / Decimal(str(count)) if count > 0 else None,
        }

    return result


def analyze_signal_correlation(trades):
    """Co-occurrence rates and marginal PnL contribution for signal pairs.

    Args:
        trades: Iterable of objects with entry_reason (dict) and pnl (Decimal).

    Returns:
        Dict mapping (sig_a, sig_b) tuple -> {
            co_fire_count, co_total_pnl, co_avg_pnl,
            only_a_count, only_a_avg_pnl,
            only_b_count, only_b_avg_pnl,
        }.
    """
    # Collect all signal IDs that ever fired
    all_signal_ids = set()
    trade_list = []
    for trade in trades:
        reason = trade.entry_reason
        if not isinstance(reason, dict):
            continue
        fired_signals = {sid for sid, v in reason.items() if v}
        all_signal_ids.update(fired_signals)
        trade_list.append((fired_signals, Decimal(str(trade.pnl))))

    if len(all_signal_ids) < 2:
        return {}

    result = {}
    for sig_a, sig_b in combinations(sorted(all_signal_ids), 2):
        co_pnls = []
        only_a_pnls = []
        only_b_pnls = []

        for fired_signals, pnl in trade_list:
            has_a = sig_a in fired_signals
            has_b = sig_b in fired_signals
            if has_a and has_b:
                co_pnls.append(pnl)
            elif has_a:
                only_a_pnls.append(pnl)
            elif has_b:
                only_b_pnls.append(pnl)

        result[(sig_a, sig_b)] = {
            'co_fire_count': len(co_pnls),
            'co_total_pnl': sum(co_pnls) if co_pnls else ZERO,
            'co_avg_pnl': sum(co_pnls) / len(co_pnls) if co_pnls else None,
            'only_a_count': len(only_a_pnls),
            'only_a_avg_pnl': sum(only_a_pnls) / len(only_a_pnls) if only_a_pnls else None,
            'only_b_count': len(only_b_pnls),
            'only_b_avg_pnl': sum(only_b_pnls) / len(only_b_pnls) if only_b_pnls else None,
        }

    return result
