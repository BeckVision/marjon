"""Management command to benchmark OHLCV fetch speed and gateway performance.

No database writes — fetches real data via the GeckoTerminal connector and
discards it. Measures per-call latency, per-gateway performance, and error rates.

Usage:
    python manage.py benchmark_ohlcv --coins 20
    python manage.py benchmark_ohlcv --coins 20 --sleep 2
    python manage.py benchmark_ohlcv --coins 20 --gateways direct
    python manage.py benchmark_ohlcv --coins 20 --gateways 1,3
    python manage.py benchmark_ohlcv --coins 20 --gateways all --sleep 0
    python manage.py benchmark_ohlcv --coins 100 --workers 6
"""

import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from pipeline.connectors.geckoterminal import (
    DIRECT_URL,
    fetch_ohlcv,
    override_gateway_urls,
)
from warehouse.models import MigratedCoin, PoolMapping

# Region labels for nicer output
REGION_LABELS = {
    'ap-southeast-2': 'Sydney',
    'us-east-1': 'Virginia',
    'eu-west-1': 'Ireland',
    'ap-northeast-1': 'Tokyo',
    'us-west-2': 'Oregon',
    'eu-central-1': 'Frankfurt',
}


def _label_for_url(url):
    """Return a short label like 'Tokyo' for a gateway URL."""
    if url == DIRECT_URL:
        return 'direct'
    for region, city in REGION_LABELS.items():
        if region in url:
            return f"{city}"
    return url[:30]


class Command(BaseCommand):
    help = "Benchmark OHLCV fetch speed and API gateway latency (no DB writes)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--coins', type=int, default=5,
            help='Number of coins to benchmark (default: 5)',
        )
        parser.add_argument(
            '--sleep', type=float, default=0,
            help='Seconds to sleep between calls (default: 0, ignored with --workers)',
        )
        parser.add_argument(
            '--gateways', type=str, default='all',
            help=(
                'Gateway selection: "all" (round-robin, default), '
                '"direct" (no gateway), or comma-separated numbers '
                'e.g. "1,3" (matches GATEWAY_URL_1, GATEWAY_URL_3)'
            ),
        )
        parser.add_argument(
            '--workers', type=int, default=1,
            help='Number of concurrent workers (default: 1 = serial)',
        )

    def handle(self, *args, **options):
        n = options['coins']
        sleep_secs = options['sleep']
        gw_mode = options['gateways']
        workers = options['workers']

        gateway_urls = self._resolve_gateways(gw_mode)

        mappings = list(
            PoolMapping.objects
            .select_related('coin')
            .filter(dex='pumpswap')[:n]
        )

        if not mappings:
            self.stderr.write("No PoolMapping rows found. Run pool mapping first.")
            return

        # Header
        gw_labels = ', '.join(_label_for_url(u) for u in gateway_urls)
        mode_label = f"workers={workers}" if workers > 1 else f"sleep={sleep_secs}s"
        self.stdout.write(
            f"Benchmarking {len(mappings)} coins | "
            f"{mode_label} | gateways: {gw_labels}\n"
        )
        self.stdout.write(
            f"{'#':>3s}  {'Coin':15s}  {'Gateway':10s}  "
            f"{'Latency':>8s}  {'Candles':>7s}  {'Status'}"
        )
        self.stdout.write("-" * 75)

        results = []
        wall_start = time.monotonic()

        def _fetch_one(idx, mapping):
            pool_address = mapping.pool_address
            coin = mapping.coin

            start = coin.anchor_event
            end = start + MigratedCoin.OBSERVATION_WINDOW_END

            t0 = time.monotonic()
            try:
                candles, meta = fetch_ohlcv(pool_address, start, end)
                elapsed = time.monotonic() - t0

                result = {
                    'idx': idx,
                    'coin': coin,
                    'gateway': meta['gateways_used'][0],
                    'latency': elapsed,
                    'candles': len(candles),
                    'status': 'ok',
                }
            except Exception as e:
                elapsed = time.monotonic() - t0
                result = {
                    'idx': idx,
                    'coin': coin,
                    'gateway': 'unknown',
                    'latency': elapsed,
                    'candles': 0,
                    'status': str(e)[:50],
                }
            return result

        # Use context manager to temporarily override gateways, restoring on exit
        with override_gateway_urls(gateway_urls):
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
                    if sleep_secs and i < len(mappings):
                        time.sleep(sleep_secs)

        wall_elapsed = time.monotonic() - wall_start

        # Sort results by original index for display
        results.sort(key=lambda r: r['idx'])
        for r in results:
            coin = r['coin']
            coin_label = (coin.symbol or coin.mint_address[:12])[:15]
            label = _label_for_url(r['gateway'])
            self.stdout.write(
                f"{r['idx']:3d}  {coin_label:15s}  {label:10s}  "
                f"{r['latency']:7.3f}s  {r['candles']:7d}  {r['status']}"
            )

        self._print_gateway_breakdown(results)
        self._print_summary(results, wall_elapsed, workers)

    def _resolve_gateways(self, mode):
        """Parse --gateways flag into a list of URLs."""
        configured = settings.GATEWAY_URLS

        if mode == 'all':
            if not configured:
                return [DIRECT_URL]
            return configured

        if mode == 'direct':
            return [DIRECT_URL]

        try:
            indices = [int(x.strip()) for x in mode.split(',')]
        except ValueError:
            raise CommandError(
                f"Invalid --gateways value: '{mode}'. "
                f"Use 'all', 'direct', or numbers like '1,3'."
            )

        urls = []
        for idx in indices:
            if idx < 1 or idx > len(configured):
                raise CommandError(
                    f"Gateway {idx} not configured. "
                    f"Available: 1-{len(configured)}."
                )
            urls.append(configured[idx - 1])

        if not urls:
            raise CommandError("No gateways selected.")
        return urls

    def _print_gateway_breakdown(self, results):
        gw_stats = defaultdict(lambda: {'latencies': [], 'errors': 0, 'candles': 0})
        for r in results:
            gw = r['gateway']
            if r['status'] == 'ok':
                gw_stats[gw]['latencies'].append(r['latency'])
                gw_stats[gw]['candles'] += r['candles']
            else:
                gw_stats[gw]['errors'] += 1

        self.stdout.write(f"\n--- Per-gateway breakdown ---")
        self.stdout.write(
            f"{'Gateway':10s}  {'Calls':>5s}  {'Avg':>7s}  "
            f"{'Min':>7s}  {'Max':>7s}  {'Candles':>7s}  {'Errors':>6s}"
        )
        self.stdout.write("-" * 65)

        for gw in sorted(gw_stats, key=lambda g: _label_for_url(g)):
            stats = gw_stats[gw]
            lats = stats['latencies']
            label = _label_for_url(gw)
            if lats:
                avg = sum(lats) / len(lats)
                self.stdout.write(
                    f"{label:10s}  {len(lats):5d}  {avg:6.3f}s  "
                    f"{min(lats):6.3f}s  {max(lats):6.3f}s  "
                    f"{stats['candles']:7d}  {stats['errors']:6d}"
                )
            else:
                self.stdout.write(
                    f"{label:10s}  {0:5d}      n/a      n/a      n/a  "
                    f"{stats['candles']:7d}  {stats['errors']:6d}"
                )

    def _print_summary(self, results, wall_elapsed, workers):
        ok = [r for r in results if r['status'] == 'ok']
        errors = len(results) - len(ok)
        total_candles = sum(r['candles'] for r in results)
        ok_latencies = [r['latency'] for r in ok]

        self.stdout.write(f"\n--- Summary ---")
        self.stdout.write(f"Calls: {len(ok)} ok, {errors} errors")
        self.stdout.write(f"Candles: {total_candles} total")
        if ok_latencies:
            self.stdout.write(
                f"Latency: avg={sum(ok_latencies)/len(ok_latencies):.3f}s, "
                f"min={min(ok_latencies):.3f}s, max={max(ok_latencies):.3f}s"
            )
            fetch_time = sum(ok_latencies)
            self.stdout.write(
                f"Wall clock: {wall_elapsed:.1f}s "
                f"(sum of latencies: {fetch_time:.1f}s, workers: {workers})"
            )
            if wall_elapsed > 0:
                throughput = len(ok) / (wall_elapsed / 60)
                self.stdout.write(f"Throughput: {throughput:.1f} calls/min")
