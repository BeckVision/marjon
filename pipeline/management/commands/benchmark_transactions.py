"""Benchmark RD-001 transaction fetch speed and identify bottlenecks.

No database writes — fetches real data via connectors and discards it.
Measures per-coin latency, phase breakdown (RPC discovery vs REST parsing),
rate-limit impact, volume distribution, and identifies the dominant bottleneck.

Usage:
    python manage.py benchmark_transactions --coins 10
    python manage.py benchmark_transactions --coins 20 --workers 4
    python manage.py benchmark_transactions --coins 10 --source helius
    python manage.py benchmark_transactions --coins 5 --workers 1 --sleep 1
    python manage.py benchmark_transactions --coins 10 --sort random
"""

import math
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from django.core.management.base import BaseCommand

from warehouse.models import MigratedCoin, PoolMapping

from pipeline.connectors import helius as helius_conn
from pipeline.connectors import shyft as shyft_conn
from pipeline.management.commands.fetch_transactions import (
    SHYFT_RETENTION_DAYS,
    _select_source,
)


class Command(BaseCommand):
    help = "Benchmark RD-001 transaction fetch speed and find bottlenecks (no DB writes)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--coins', type=int, default=5,
            help='Number of coins to benchmark (default: 5)',
        )
        parser.add_argument(
            '--workers', type=int, default=1,
            help='Concurrent workers (default: 1 = serial)',
        )
        parser.add_argument(
            '--source', type=str, default='auto',
            choices=['shyft', 'helius', 'auto'],
            help='Data source (default: auto)',
        )
        parser.add_argument(
            '--sleep', type=float, default=0,
            help='Seconds between coins in serial mode (default: 0)',
        )
        parser.add_argument(
            '--sort', type=str, default='active',
            choices=['active', 'recent', 'random'],
            help='Coin selection order (default: active = most recent first)',
        )
        parser.add_argument(
            '--parse-workers', type=int, default=1,
            help='Concurrent workers for Phase 2 parsing within each coin '
                 '(default: 1 = sequential). Uses per-key rate limiting.',
        )

    def handle(self, *args, **options):
        n = options['coins']
        workers = options['workers']
        source = options['source']
        sleep_secs = options['sleep']
        sort_mode = options['sort']
        parse_workers = options['parse_workers']

        mappings = _select_coins(n, source, sort_mode)
        if not mappings:
            self.stderr.write("No coins with pool mappings found.")
            return

        mode_label = (
            f"workers={workers}" if workers > 1
            else f"serial, sleep={sleep_secs}s"
        )
        if parse_workers > 1:
            mode_label += f", parse_workers={parse_workers}"
        shyft_keys = len(settings.SHYFT_API_KEYS)
        helius_keys = len(settings.HELIUS_API_KEYS)
        self.stdout.write(
            f"\nBenchmarking {len(mappings)} coins | "
            f"source={source} | {mode_label}"
        )
        self.stdout.write(
            f"API keys: {shyft_keys} Shyft, {helius_keys} Helius"
        )
        self.stdout.write(
            f"Rate limit: {shyft_conn.RATE_LIMIT_SLEEP}s/key (Shyft), "
            f"{helius_conn.RATE_LIMIT_SLEEP}s/key (Helius)\n"
        )

        self.stdout.write(
            f"{'#':>3s}  {'Coin':15s}  {'Src':7s}  "
            f"{'Sigs':>6s}  {'Filt':>6s}  {'Parsed':>6s}  "
            f"{'RPC':>4s}  {'REST':>4s}  "
            f"{'Phase1':>7s}  {'Phase2':>7s}  {'Total':>7s}  {'Status'}"
        )
        self.stdout.write("-" * 105)

        results = []
        wall_start = time.monotonic()

        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_fetch_one, i, m, source, parse_workers): i
                    for i, m in enumerate(mappings, 1)
                }
                for future in as_completed(futures):
                    results.append(future.result())
        else:
            for i, mapping in enumerate(mappings, 1):
                result = _fetch_one(i, mapping, source, parse_workers)
                results.append(result)
                _print_row(self, result)
                if sleep_secs and i < len(mappings):
                    time.sleep(sleep_secs)

        wall_elapsed = time.monotonic() - wall_start

        if workers > 1:
            results.sort(key=lambda r: r['idx'])
            for r in results:
                _print_row(self, r)

        _print_phase_analysis(self, results)
        _print_volume_distribution(self, results)
        _print_source_breakdown(self, results)
        _print_summary(self, results, wall_elapsed, workers)


# ---------------------------------------------------------------------------
# Coin selection
# ---------------------------------------------------------------------------

def _select_coins(n, source, sort_mode):
    """Select coins for benchmarking based on source and sort mode."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    qs = PoolMapping.objects.select_related('coin').filter(dex='pumpswap')

    if source == 'shyft':
        cutoff = now - timedelta(days=SHYFT_RETENTION_DAYS)
        qs = qs.filter(coin__anchor_event__gte=cutoff)
    elif source == 'helius':
        cutoff = now - timedelta(days=SHYFT_RETENTION_DAYS)
        qs = qs.filter(coin__anchor_event__lt=cutoff)

    if sort_mode == 'random':
        qs = qs.order_by('?')
    else:
        # 'active' and 'recent' both sort by newest first
        qs = qs.order_by('-coin__anchor_event')

    return list(qs[:n])


# ---------------------------------------------------------------------------
# Per-coin fetch + metrics
# ---------------------------------------------------------------------------

def _fetch_one(idx, mapping, source_pref, parse_workers=1):
    """Fetch one coin and return detailed metrics."""
    pool_address = mapping.pool_address
    coin = mapping.coin

    coin_source = (
        _select_source(coin) if source_pref == 'auto' else source_pref
    )

    start = coin.anchor_event
    end = start + MigratedCoin.OBSERVATION_WINDOW_END

    if coin_source == 'shyft':
        conn = shyft_conn
    else:
        conn = helius_conn

    sig_limit = conn.SIG_LIMIT
    parse_batch = conn.PARSE_BATCH_SIZE
    rate_sleep = conn.RATE_LIMIT_SLEEP

    t0 = time.monotonic()
    try:
        # Phase 1: signature discovery
        t_p1 = time.monotonic()
        if coin_source == 'shyft':
            raw_sigs = conn._fetch_signatures(pool_address, start, end)
        else:
            raw_sigs, _ = conn._fetch_signatures(pool_address, start, end)
        phase1 = time.monotonic() - t_p1

        filtered = conn._filter_signatures(raw_sigs, start, end)

        # Phase 2: parse (with optional intra-coin parallelism)
        t_p2 = time.monotonic()
        if filtered:
            if coin_source == 'shyft':
                parsed = conn._parse_selected(
                    filtered, max_workers=parse_workers,
                )
            else:
                parsed, _ = conn._parse_transactions(
                    filtered, max_workers=parse_workers,
                )
        else:
            parsed = []
        phase2 = time.monotonic() - t_p2

        total = time.monotonic() - t0

        rpc_calls = max(1, math.ceil(len(raw_sigs) / sig_limit))
        rest_calls = (
            math.ceil(len(filtered) / parse_batch) if filtered else 0
        )
        est_sleep = max(0, rest_calls - 1) * rate_sleep

        return {
            'idx': idx,
            'coin': coin,
            'source': coin_source,
            'sigs': len(raw_sigs),
            'filtered': len(filtered),
            'parsed': len(parsed),
            'rpc_calls': rpc_calls,
            'rest_calls': rest_calls,
            'phase1': phase1,
            'phase2': phase2,
            'total': total,
            'est_sleep': est_sleep,
            'rate_sleep': rate_sleep,
            'status': 'ok',
        }
    except Exception as e:
        total = time.monotonic() - t0
        return {
            'idx': idx,
            'coin': coin,
            'source': coin_source,
            'sigs': 0,
            'filtered': 0,
            'parsed': 0,
            'rpc_calls': 0,
            'rest_calls': 0,
            'phase1': 0,
            'phase2': 0,
            'total': total,
            'est_sleep': 0,
            'rate_sleep': 0,
            'status': str(e)[:60],
        }


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_row(cmd, r):
    coin_label = (r['coin'].symbol or r['coin'].mint_address[:12])[:15]
    cmd.stdout.write(
        f"{r['idx']:3d}  {coin_label:15s}  {r['source']:7s}  "
        f"{r['sigs']:6d}  {r['filtered']:6d}  {r['parsed']:6d}  "
        f"{r['rpc_calls']:4d}  {r['rest_calls']:4d}  "
        f"{r['phase1']:6.1f}s  {r['phase2']:6.1f}s  "
        f"{r['total']:6.1f}s  {r['status']}"
    )


def _pct(val, total):
    return (val / total * 100) if total > 0 else 0


def _percentile(sorted_vals, p):
    """Compute p-th percentile from a sorted list."""
    if not sorted_vals:
        return 0
    k = (len(sorted_vals) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    d = k - f
    return sorted_vals[f] + d * (sorted_vals[c] - sorted_vals[f])


# ---------------------------------------------------------------------------
# Analysis sections
# ---------------------------------------------------------------------------

def _print_phase_analysis(cmd, results):
    """Break down where time is spent and identify the dominant bottleneck."""
    ok = [r for r in results if r['status'] == 'ok']
    if not ok:
        return

    total_phase1 = sum(r['phase1'] for r in ok)
    total_phase2 = sum(r['phase2'] for r in ok)
    total_time = total_phase1 + total_phase2
    if total_time == 0:
        return

    total_rpc = sum(r['rpc_calls'] for r in ok)
    total_rest = sum(r['rest_calls'] for r in ok)
    total_sleep = sum(r['est_sleep'] for r in ok)
    est_api = max(0, total_phase2 - total_sleep)

    p1_pct = _pct(total_phase1, total_time)
    p2_pct = _pct(total_phase2, total_time)
    sleep_pct = _pct(total_sleep, total_phase2)
    api_pct = 100 - sleep_pct

    cmd.stdout.write(f"\n{'='*65}")
    cmd.stdout.write("PHASE ANALYSIS")
    cmd.stdout.write(f"{'='*65}")

    cmd.stdout.write(f"\nPhase 1 — RPC signature discovery:")
    cmd.stdout.write(f"  Calls: {total_rpc}")
    cmd.stdout.write(f"  Time:  {total_phase1:.1f}s ({p1_pct:.1f}% of total)")
    if total_rpc > 0:
        cmd.stdout.write(
            f"  Avg per call: {total_phase1 / total_rpc:.2f}s"
        )

    cmd.stdout.write(f"\nPhase 2 — REST/Enhanced parsing:")
    cmd.stdout.write(f"  Calls: {total_rest}")
    cmd.stdout.write(f"  Time:  {total_phase2:.1f}s ({p2_pct:.1f}% of total)")
    if total_rest > 0:
        cmd.stdout.write(
            f"  Avg per call: {total_phase2 / total_rest:.2f}s"
        )
    cmd.stdout.write(
        f"  Rate-limit sleep (est): ~{total_sleep:.1f}s "
        f"({sleep_pct:.0f}% of Phase 2)"
    )
    cmd.stdout.write(
        f"  Actual API time (est):  ~{est_api:.1f}s "
        f"({api_pct:.0f}% of Phase 2)"
    )
    if total_rest > 0:
        cmd.stdout.write(
            f"  Avg API latency/call:   ~{est_api / total_rest:.2f}s "
            f"(excl. sleep)"
        )

    # Bottleneck identification
    cmd.stdout.write(f"\n--- Bottleneck ---")
    if p2_pct > 80:
        if sleep_pct > 40:
            effective_rate = total_rest / total_phase2 if total_phase2 > 0 else 0
            cmd.stdout.write(
                f"  RATE LIMITING is the primary bottleneck.\n"
                f"  ~{sleep_pct:.0f}% of Phase 2 is sleep between calls.\n"
                f"  Effective rate: ~{effective_rate:.1f} calls/sec\n"
                f"  More API keys = directly higher throughput."
            )
        else:
            avg_api = est_api / total_rest if total_rest > 0 else 0
            cmd.stdout.write(
                f"  API LATENCY is the primary bottleneck.\n"
                f"  Server response time (~{avg_api:.1f}s/call) dominates.\n"
                f"  More workers or smaller batches may help."
            )
    elif p1_pct > 40:
        cmd.stdout.write(
            f"  RPC DISCOVERY takes a significant share ({p1_pct:.0f}%).\n"
            f"  High sig counts require many pagination calls."
        )
    else:
        cmd.stdout.write("  No single dominant bottleneck.")


def _print_volume_distribution(cmd, results):
    """Show distribution of transaction volumes and identify outliers."""
    ok = [r for r in results if r['status'] == 'ok']
    if not ok:
        return

    sigs = sorted(r['sigs'] for r in ok)
    parsed = sorted(r['parsed'] for r in ok)

    total_sigs = sum(r['sigs'] for r in ok)
    total_filtered = sum(r['filtered'] for r in ok)
    filter_rate = _pct(total_sigs - total_filtered, total_sigs)

    cmd.stdout.write(f"\n{'='*65}")
    cmd.stdout.write("VOLUME DISTRIBUTION")
    cmd.stdout.write(f"{'='*65}")
    cmd.stdout.write(
        f"\nSigs per coin:    "
        f"min={sigs[0]:,}  "
        f"p25={_percentile(sigs, 25):,.0f}  "
        f"median={_percentile(sigs, 50):,.0f}  "
        f"p75={_percentile(sigs, 75):,.0f}  "
        f"max={sigs[-1]:,}"
    )
    cmd.stdout.write(
        f"Parsed per coin:  "
        f"min={parsed[0]:,}  "
        f"p25={_percentile(parsed, 25):,.0f}  "
        f"median={_percentile(parsed, 50):,.0f}  "
        f"p75={_percentile(parsed, 75):,.0f}  "
        f"max={parsed[-1]:,}"
    )
    cmd.stdout.write(
        f"Filter rate: {filter_rate:.1f}% dropped (failed/out-of-window)"
    )

    # REST calls distribution (shows cost variance)
    rest = sorted(r['rest_calls'] for r in ok)
    cmd.stdout.write(
        f"REST calls/coin:  "
        f"min={rest[0]}  "
        f"median={_percentile(rest, 50):.0f}  "
        f"max={rest[-1]}"
    )

    # Outlier detection (IQR method)
    if len(ok) >= 4:
        p75 = _percentile(sigs, 75)
        p25 = _percentile(sigs, 25)
        iqr = p75 - p25
        threshold = p75 + 1.5 * iqr
        outliers = [r for r in ok if r['sigs'] > threshold]
        if outliers:
            cmd.stdout.write(
                f"\nOutliers (>{threshold:,.0f} sigs):"
            )
            for r in sorted(outliers, key=lambda x: -x['sigs']):
                label = (
                    r['coin'].symbol or r['coin'].mint_address[:12]
                )[:15]
                cmd.stdout.write(
                    f"  {label}: {r['sigs']:,} sigs, "
                    f"{r['rest_calls']} REST calls, "
                    f"{r['total']:.1f}s total"
                )


def _print_source_breakdown(cmd, results):
    """Compare performance across data sources (Shyft vs Helius)."""
    ok = [r for r in results if r['status'] == 'ok']
    by_source = defaultdict(list)
    for r in ok:
        by_source[r['source']].append(r)

    if len(by_source) <= 1:
        return

    cmd.stdout.write(f"\n{'='*65}")
    cmd.stdout.write("SOURCE COMPARISON")
    cmd.stdout.write(f"{'='*65}")

    sources = sorted(by_source)
    header = f"{'':22s}" + "".join(f"  {s:>12s}" for s in sources)
    cmd.stdout.write(header)
    cmd.stdout.write("-" * (22 + 14 * len(sources)))

    rows = [
        ("Coins",           lambda rs: len(rs),                             "d",  ""),
        ("Avg sigs/coin",   lambda rs: sum(r['sigs'] for r in rs)/len(rs),  ",.0f",""),
        ("Avg parsed/coin", lambda rs: sum(r['parsed'] for r in rs)/len(rs),",.0f",""),
        ("Avg Phase 1",     lambda rs: sum(r['phase1'] for r in rs)/len(rs),".1f", "s"),
        ("Avg Phase 2",     lambda rs: sum(r['phase2'] for r in rs)/len(rs),".1f", "s"),
        ("Avg total",       lambda rs: sum(r['total'] for r in rs)/len(rs), ".1f", "s"),
        ("Rate limit/call", lambda rs: rs[0]['rate_sleep'],                  ".1f", "s"),
        ("Avg REST calls",  lambda rs: sum(r['rest_calls'] for r in rs)/len(rs),".0f",""),
    ]

    for name, fn, fmt, suffix in rows:
        line = f"{name:22s}"
        for src in sources:
            try:
                val = fn(by_source[src])
                line += f"  {format(val, fmt) + suffix:>12s}"
            except Exception:
                line += f"  {'n/a':>12s}"
        cmd.stdout.write(line)


def _print_summary(cmd, results, wall_elapsed, workers):
    """Print final summary with throughput and theoretical ceiling."""
    ok = [r for r in results if r['status'] == 'ok']
    errors = len(results) - len(ok)

    total_sigs = sum(r['sigs'] for r in ok)
    total_parsed = sum(r['parsed'] for r in ok)
    total_rpc = sum(r['rpc_calls'] for r in ok)
    total_rest = sum(r['rest_calls'] for r in ok)

    cmd.stdout.write(f"\n{'='*65}")
    cmd.stdout.write("SUMMARY")
    cmd.stdout.write(f"{'='*65}")
    cmd.stdout.write(f"\nCoins: {len(ok)} ok, {errors} errors")
    cmd.stdout.write(
        f"Signatures: {total_sigs:,} discovered, "
        f"{total_parsed:,} parsed"
    )
    cmd.stdout.write(
        f"API calls: {total_rpc} RPC + {total_rest} REST "
        f"= {total_rpc + total_rest} total"
    )

    if not ok:
        return

    total_times = sorted(r['total'] for r in ok)
    fetch_time = sum(total_times)

    cmd.stdout.write(
        f"\nPer-coin latency:  "
        f"min={total_times[0]:.1f}s  "
        f"p50={_percentile(total_times, 50):.1f}s  "
        f"p95={_percentile(total_times, 95):.1f}s  "
        f"max={total_times[-1]:.1f}s"
    )
    cmd.stdout.write(
        f"Wall clock: {wall_elapsed:.1f}s "
        f"(sum of latencies: {fetch_time:.1f}s, workers: {workers})"
    )

    if wall_elapsed > 0:
        speedup = fetch_time / wall_elapsed
        coins_per_min = len(ok) / (wall_elapsed / 60)
        txs_per_min = total_parsed / (wall_elapsed / 60) if total_parsed else 0
        cmd.stdout.write(f"Speedup: {speedup:.1f}x")
        cmd.stdout.write(
            f"Throughput: {coins_per_min:.1f} coins/min, "
            f"{txs_per_min:,.0f} txs/min"
        )

    # Theoretical ceiling per source
    by_source = defaultdict(list)
    for r in ok:
        by_source[r['source']].append(r)

    cmd.stdout.write(f"\n--- Theoretical ceiling ---")
    for src in sorted(by_source):
        rs = by_source[src]
        if src == 'shyft':
            n_keys = len(settings.SHYFT_API_KEYS)
        else:
            n_keys = len(settings.HELIUS_API_KEYS)

        rate_sleep = rs[0]['rate_sleep']
        calls_per_sec = n_keys / rate_sleep if rate_sleep > 0 else 0
        avg_rest_per_coin = sum(r['rest_calls'] for r in rs) / len(rs)
        if avg_rest_per_coin > 0:
            coins_per_min = 60 * calls_per_sec / avg_rest_per_coin
        else:
            coins_per_min = 0

        cmd.stdout.write(
            f"  {src}: {n_keys} keys × "
            f"{1/rate_sleep:.0f} calls/sec/key "
            f"= {calls_per_sec:.0f} calls/sec → "
            f"~{coins_per_min:.0f} coins/min "
            f"(at avg {avg_rest_per_coin:.0f} REST calls/coin)"
        )
