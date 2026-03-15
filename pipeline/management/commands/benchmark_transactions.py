"""Management command to benchmark RD-001 transaction fetch speed.

No database writes — fetches real data via connectors and discards it.
Measures per-coin latency, phase breakdown (RPC discovery vs REST parsing),
and throughput at different worker counts.

Usage:
    python manage.py benchmark_transactions --coins 10
    python manage.py benchmark_transactions --coins 20 --workers 4
    python manage.py benchmark_transactions --coins 10 --source helius
    python manage.py benchmark_transactions --coins 5 --workers 1 --sleep 1
"""

import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand
from django.conf import settings

from warehouse.models import MigratedCoin, PoolMapping

from pipeline.management.commands.fetch_transactions import (
    SHYFT_RETENTION_DAYS,
    _select_source,
)


class Command(BaseCommand):
    help = "Benchmark RD-001 transaction fetch speed (no DB writes)"

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
            help='Seconds between calls in serial mode (default: 0)',
        )
        parser.add_argument(
            '--sort', type=str, default='active',
            choices=['active', 'recent', 'random'],
            help='Coin selection order (default: active = most trades first)',
        )

    def handle(self, *args, **options):
        n = options['coins']
        workers = options['workers']
        source = options['source']
        sleep_secs = options['sleep']
        sort_mode = options['sort']

        mappings = self._select_coins(n, source, sort_mode)
        if not mappings:
            self.stderr.write("No coins with pool mappings found.")
            return

        mode_label = f"workers={workers}" if workers > 1 else f"sleep={sleep_secs}s"
        self.stdout.write(
            f"Benchmarking {len(mappings)} coins | "
            f"source={source} | {mode_label}\n"
        )
        self.stdout.write(
            f"{'#':>3s}  {'Coin':15s}  {'Source':7s}  "
            f"{'Sigs':>6s}  {'Parsed':>6s}  {'Phase1':>7s}  "
            f"{'Phase2':>7s}  {'Total':>7s}  {'Status'}"
        )
        self.stdout.write("-" * 90)

        results = []
        wall_start = time.monotonic()

        def _fetch_one(idx, mapping):
            pool_address = mapping.pool_address
            coin = mapping.coin

            coin_source = (
                _select_source(coin) if source == 'auto' else source
            )

            start = coin.anchor_event
            end = start + MigratedCoin.OBSERVATION_WINDOW_END

            # Import connector based on source
            if coin_source == 'shyft':
                from pipeline.connectors.shyft import (
                    _fetch_signatures, _filter_signatures, _parse_selected,
                )
            else:
                from pipeline.connectors.helius import (
                    _fetch_signatures, _filter_signatures,
                    _parse_transactions,
                )

            t0 = time.monotonic()
            try:
                # Phase 1: signature discovery
                t_phase1_start = time.monotonic()
                if coin_source == 'shyft':
                    raw_sigs = _fetch_signatures(pool_address, start, end)
                else:
                    raw_sigs, _ = _fetch_signatures(pool_address, start, end)
                t_phase1 = time.monotonic() - t_phase1_start

                filtered = _filter_signatures(raw_sigs, start, end)

                # Phase 2: parse
                t_phase2_start = time.monotonic()
                if filtered:
                    if coin_source == 'shyft':
                        parsed = _parse_selected(filtered)
                    else:
                        parsed, _ = _parse_transactions(filtered)
                else:
                    parsed = []
                t_phase2 = time.monotonic() - t_phase2_start

                total = time.monotonic() - t0

                return {
                    'idx': idx,
                    'coin': coin,
                    'source': coin_source,
                    'sigs': len(raw_sigs),
                    'filtered': len(filtered),
                    'parsed': len(parsed),
                    'phase1': t_phase1,
                    'phase2': t_phase2,
                    'total': total,
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
                    'phase1': 0,
                    'phase2': 0,
                    'total': total,
                    'status': str(e)[:50],
                }

        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_fetch_one, i, m): i
                    for i, m in enumerate(mappings, 1)
                }
                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
        else:
            for i, mapping in enumerate(mappings, 1):
                result = _fetch_one(i, mapping)
                results.append(result)
                # Print live in serial mode
                r = result
                coin_label = (r['coin'].symbol or r['coin'].mint_address[:12])[:15]
                self.stdout.write(
                    f"{r['idx']:3d}  {coin_label:15s}  {r['source']:7s}  "
                    f"{r['sigs']:6d}  {r['parsed']:6d}  "
                    f"{r['phase1']:6.1f}s  {r['phase2']:6.1f}s  "
                    f"{r['total']:6.1f}s  {r['status']}"
                )
                if sleep_secs and i < len(mappings):
                    time.sleep(sleep_secs)

        wall_elapsed = time.monotonic() - wall_start

        # Print results (for concurrent mode, print all at once sorted)
        if workers > 1:
            results.sort(key=lambda r: r['idx'])
            for r in results:
                coin_label = (r['coin'].symbol or r['coin'].mint_address[:12])[:15]
                self.stdout.write(
                    f"{r['idx']:3d}  {coin_label:15s}  {r['source']:7s}  "
                    f"{r['sigs']:6d}  {r['parsed']:6d}  "
                    f"{r['phase1']:6.1f}s  {r['phase2']:6.1f}s  "
                    f"{r['total']:6.1f}s  {r['status']}"
                )

        self._print_summary(results, wall_elapsed, workers, source)

    def _select_coins(self, n, source, sort_mode):
        """Select coins for benchmarking."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)

        qs = PoolMapping.objects.select_related('coin').filter(dex='pumpswap')

        # Filter by source
        if source == 'shyft':
            cutoff = now - timedelta(days=SHYFT_RETENTION_DAYS)
            qs = qs.filter(coin__anchor_event__gte=cutoff)
        elif source == 'helius':
            cutoff = now - timedelta(days=SHYFT_RETENTION_DAYS)
            qs = qs.filter(coin__anchor_event__lt=cutoff)

        if sort_mode == 'recent':
            qs = qs.order_by('-coin__anchor_event')
        elif sort_mode == 'random':
            qs = qs.order_by('?')
        else:
            # 'active' — most recent anchor first (approximates activity)
            qs = qs.order_by('-coin__anchor_event')

        return list(qs[:n])

    def _print_summary(self, results, wall_elapsed, workers, source):
        ok = [r for r in results if r['status'] == 'ok']
        errors = len(results) - len(ok)

        total_sigs = sum(r['sigs'] for r in ok)
        total_parsed = sum(r['parsed'] for r in ok)
        phase1_times = [r['phase1'] for r in ok]
        phase2_times = [r['phase2'] for r in ok]
        total_times = [r['total'] for r in ok]

        self.stdout.write(f"\n--- Summary ---")
        self.stdout.write(f"Coins: {len(ok)} ok, {errors} errors")
        self.stdout.write(f"Signatures: {total_sigs:,} discovered")
        self.stdout.write(f"Transactions: {total_parsed:,} parsed")

        if ok:
            self.stdout.write(
                f"\nPhase 1 (sig discovery): "
                f"avg={sum(phase1_times)/len(ok):.1f}s, "
                f"min={min(phase1_times):.1f}s, max={max(phase1_times):.1f}s"
            )
            self.stdout.write(
                f"Phase 2 (parse/conform): "
                f"avg={sum(phase2_times)/len(ok):.1f}s, "
                f"min={min(phase2_times):.1f}s, max={max(phase2_times):.1f}s"
            )
            self.stdout.write(
                f"Per-coin total: "
                f"avg={sum(total_times)/len(ok):.1f}s, "
                f"min={min(total_times):.1f}s, max={max(total_times):.1f}s"
            )

            fetch_time = sum(total_times)
            self.stdout.write(
                f"\nWall clock: {wall_elapsed:.1f}s "
                f"(sum of latencies: {fetch_time:.1f}s, workers: {workers})"
            )
            if wall_elapsed > 0:
                speedup = fetch_time / wall_elapsed
                throughput_coins = len(ok) / (wall_elapsed / 60)
                throughput_txs = total_parsed / (wall_elapsed / 60)
                self.stdout.write(f"Speedup: {speedup:.1f}x")
                self.stdout.write(
                    f"Throughput: {throughput_coins:.1f} coins/min, "
                    f"{throughput_txs:.0f} txs/min"
                )

        # Source breakdown
        source_stats = defaultdict(lambda: {'count': 0, 'sigs': 0, 'parsed': 0, 'time': 0})
        for r in ok:
            s = source_stats[r['source']]
            s['count'] += 1
            s['sigs'] += r['sigs']
            s['parsed'] += r['parsed']
            s['time'] += r['total']

        if len(source_stats) > 1:
            self.stdout.write(f"\n--- Per-source breakdown ---")
            for src, s in sorted(source_stats.items()):
                self.stdout.write(
                    f"  {src}: {s['count']} coins, "
                    f"{s['sigs']:,} sigs, {s['parsed']:,} parsed, "
                    f"{s['time']:.1f}s total"
                )
