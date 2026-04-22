"""Derive U-001 FL-001 candles from canonical RD-001 trades."""

from collections import Counter
from datetime import timedelta
from decimal import Decimal

from warehouse.models import OHLCVCandle

LAMPORTS_PER_SOL = Decimal('1000000000')
DEFAULT_TOKEN_DECIMALS = 6
FIVE_MINUTES = OHLCVCandle.TEMPORAL_RESOLUTION
ONE_MINUTE = timedelta(minutes=1)


def derive_candles(trades, sol_usd_by_minute, token_decimals=DEFAULT_TOKEN_DECIMALS):
    """Derive 5-minute USD candles from RD-001 trades plus SOL/USD minute prices."""
    buckets = {}
    skipped_missing_sol_price = 0

    for trade in sorted(trades, key=lambda row: row.timestamp):
        minute = floor_timestamp(trade.timestamp, ONE_MINUTE)
        sol_usd = sol_usd_by_minute.get(minute)
        if sol_usd is None:
            skipped_missing_sol_price += 1
            continue

        token_qty = Decimal(trade.token_amount) / (Decimal(10) ** token_decimals)
        if token_qty <= 0:
            continue
        sol_qty = Decimal(trade.sol_amount) / LAMPORTS_PER_SOL
        price_usd = (sol_qty / token_qty) * sol_usd
        volume_usd = sol_qty * sol_usd
        bucket_ts = floor_timestamp(trade.timestamp, FIVE_MINUTES)

        bucket = buckets.setdefault(bucket_ts, {
            'timestamp': bucket_ts,
            'open_price': price_usd,
            'high_price': price_usd,
            'low_price': price_usd,
            'close_price': price_usd,
            'volume': Decimal('0'),
            'trade_count': 0,
        })
        bucket['high_price'] = max(bucket['high_price'], price_usd)
        bucket['low_price'] = min(bucket['low_price'], price_usd)
        bucket['close_price'] = price_usd
        bucket['volume'] += volume_usd
        bucket['trade_count'] += 1

    return (
        sorted(buckets.values(), key=lambda row: row['timestamp']),
        {'skipped_missing_sol_price': skipped_missing_sol_price},
    )


def compare_candles(
    *,
    coin_id,
    start,
    end,
    stored_candles,
    derived_candles,
    price_tolerance_pct=Decimal('0.05'),
    volume_tolerance_pct=Decimal('0.10'),
    skipped_missing_sol_price=0,
):
    """Compare stored FL-001 candles to derived candles over one sample window."""
    stored_map = {row['timestamp']: row for row in stored_candles}
    derived_map = {row['timestamp']: row for row in derived_candles}
    stored_ts = set(stored_map)
    derived_ts = set(derived_map)

    missing = sorted(derived_ts - stored_ts)
    extra = sorted(stored_ts - derived_ts)
    findings = []
    warnings = []

    if missing:
        findings.append('missing_derived_candles')
    if extra:
        findings.append('extra_stored_candles')
    if skipped_missing_sol_price:
        warnings.append('missing_sol_oracle_minutes')

    field_drift_counts = Counter()
    worst_drift = {}
    for ts in sorted(stored_ts & derived_ts):
        stored = stored_map[ts]
        derived = derived_map[ts]
        for field, tolerance in (
            ('open_price', price_tolerance_pct),
            ('high_price', price_tolerance_pct),
            ('low_price', price_tolerance_pct),
            ('close_price', price_tolerance_pct),
            ('volume', volume_tolerance_pct),
        ):
            drift = pct_diff(stored[field], derived[field])
            if drift is None:
                continue
            if drift > tolerance:
                field_drift_counts[field] += 1
            if field not in worst_drift or drift > worst_drift[field]:
                worst_drift[field] = drift

    if field_drift_counts:
        warnings.append('candle_value_drift')

    status = 'ok'
    if findings:
        status = 'finding'
    elif warnings:
        status = 'warning'

    detail = _detail_for_comparison(
        coin_id=coin_id,
        start=start,
        end=end,
        stored_count=len(stored_candles),
        derived_count=len(derived_candles),
        findings=findings,
        warnings=warnings,
        missing_count=len(missing),
        extra_count=len(extra),
        field_drift_counts=field_drift_counts,
        skipped_missing_sol_price=skipped_missing_sol_price,
    )
    return {
        'status': status,
        'detail': detail,
        'coin': coin_id,
        'findings': findings,
        'warnings': warnings,
        'missing_timestamps': [value.isoformat() for value in missing[:5]],
        'extra_timestamps': [value.isoformat() for value in extra[:5]],
        'field_drift_counts': dict(field_drift_counts),
        'worst_drift_pct': {key: str(value) for key, value in worst_drift.items()},
        'stored_count': len(stored_candles),
        'derived_count': len(derived_candles),
        'skipped_missing_sol_price': skipped_missing_sol_price,
    }


def summarize_results(results):
    return {
        'statuses': dict(Counter(row['status'] for row in results)),
        'finding_buckets': dict(
            Counter(bucket for row in results for bucket in row['findings'])
        ),
        'warning_buckets': dict(
            Counter(bucket for row in results for bucket in row['warnings'])
        ),
    }


def floor_timestamp(value, resolution):
    seconds = int(resolution.total_seconds())
    epoch = int(value.timestamp())
    floored = epoch - (epoch % seconds)
    return value.fromtimestamp(floored, tz=value.tzinfo)


def pct_diff(stored, derived):
    if stored is None or derived is None:
        return None
    stored = Decimal(stored)
    derived = Decimal(derived)
    if stored == 0 and derived == 0:
        return Decimal('0')
    if stored == 0:
        return Decimal('1')
    return abs(derived - stored) / abs(stored)


def _detail_for_comparison(
    *,
    coin_id,
    start,
    end,
    stored_count,
    derived_count,
    findings,
    warnings,
    missing_count,
    extra_count,
    field_drift_counts,
    skipped_missing_sol_price,
):
    if findings:
        return (
            f"Derived FL-001 mismatch for {coin_id} over {start} -> {end}: "
            f"stored={stored_count}, derived={derived_count}, "
            f"missing={missing_count}, extra={extra_count}."
        )
    if warnings:
        drift_summary = ', '.join(
            f'{field}={count}' for field, count in sorted(field_drift_counts.items())
        ) or 'none'
        return (
            f"Derived FL-001 partial match for {coin_id} over {start} -> {end}: "
            f"stored={stored_count}, derived={derived_count}, "
            f"drift={drift_summary}, missing_sol_minutes={skipped_missing_sol_price}."
        )
    return (
        f"Derived FL-001 matched for {coin_id} over {start} -> {end} "
        f"({derived_count} candles)."
    )
