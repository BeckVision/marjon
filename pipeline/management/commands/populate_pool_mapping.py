"""Management command to populate pool mappings via Dexscreener/GeckoTerminal fallback chain."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand

from pipeline.connectors.dexscreener import fetch_token_pools_batch as dex_fetch
from pipeline.connectors.geckoterminal import fetch_token_pools_batch as gt_fetch
from pipeline.conformance.u001_pool_mapping_dexscreener import conform as dex_conform
from pipeline.conformance.u001_pool_mapping_geckoterminal import conform as gt_conform
from pipeline.loaders.u001_pool_mapping import load_pool_mappings
from warehouse.models import MigratedCoin, PoolMapping

logger = logging.getLogger(__name__)

BATCH_SIZE = 30


def get_unmapped_tokens():
    """Return list of mint addresses that have no PoolMapping row."""
    mapped = set(
        PoolMapping.objects.values_list('coin_id', flat=True).distinct()
    )
    all_mints = list(
        MigratedCoin.objects.values_list('mint_address', flat=True)
    )
    return [m for m in all_mints if m not in mapped]


def _process_dex_batch(batch):
    """Fetch and conform a single Dexscreener batch. Returns (canonical, api_calls)."""
    raw_pairs, meta = dex_fetch(batch)
    canonical = dex_conform(raw_pairs)
    return canonical, meta['api_calls']


def _process_gt_batch(batch):
    """Fetch and conform a single GeckoTerminal batch. Returns (canonical, api_calls)."""
    raw_response, meta = gt_fetch(batch)
    canonical = gt_conform(raw_response)
    return canonical, meta['api_calls']


def run_fallback_chain(mint_addresses=None, workers=1):
    """Execute the Dexscreener -> GeckoTerminal fallback chain.

    Args:
        mint_addresses: List of mint addresses to process.
            If None, queries all unmapped tokens.
        workers: Number of concurrent workers. 1 = serial (default).

    Returns:
        dict with 'dexscreener_mapped', 'geckoterminal_mapped',
        'unmapped', 'total_processed', 'api_calls'.
    """
    if mint_addresses is None:
        mint_addresses = get_unmapped_tokens()

    if not mint_addresses:
        return {
            'dexscreener_mapped': 0,
            'geckoterminal_mapped': 0,
            'unmapped': 0,
            'total_processed': 0,
            'api_calls': 0,
        }

    total_api_calls = 0
    dex_mapped = set()

    # Stage 1 — Dexscreener
    batches = [
        mint_addresses[i:i + BATCH_SIZE]
        for i in range(0, len(mint_addresses), BATCH_SIZE)
    ]

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_process_dex_batch, batch): batch
                for batch in batches
            }
            for future in as_completed(futures):
                canonical, api_calls = future.result()
                total_api_calls += api_calls
                if canonical:
                    load_pool_mappings(canonical)
                    dex_mapped.update(r['coin_id'] for r in canonical)
    else:
        for batch in batches:
            canonical, api_calls = _process_dex_batch(batch)
            total_api_calls += api_calls
            if canonical:
                load_pool_mappings(canonical)
                dex_mapped.update(r['coin_id'] for r in canonical)

    still_unmapped = [m for m in mint_addresses if m not in dex_mapped]

    logger.info(
        "Stage 1 (Dexscreener): mapped %d of %d tokens (%d API calls)",
        len(dex_mapped), len(mint_addresses), total_api_calls,
    )

    # Stage 2 — GeckoTerminal (only Stage 1 misses)
    gt_mapped = set()
    gt_api_calls = 0

    if still_unmapped:
        gt_batches = [
            still_unmapped[i:i + BATCH_SIZE]
            for i in range(0, len(still_unmapped), BATCH_SIZE)
        ]

        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_process_gt_batch, batch): batch
                    for batch in gt_batches
                }
                for future in as_completed(futures):
                    canonical, api_calls = future.result()
                    gt_api_calls += api_calls
                    if canonical:
                        load_pool_mappings(canonical)
                        gt_mapped.update(r['coin_id'] for r in canonical)
        else:
            for idx, batch in enumerate(gt_batches):
                canonical, api_calls = _process_gt_batch(batch)
                gt_api_calls += api_calls
                if canonical:
                    load_pool_mappings(canonical)
                    gt_mapped.update(r['coin_id'] for r in canonical)

                if idx < len(gt_batches) - 1:
                    time.sleep(0.1)

    total_api_calls += gt_api_calls

    logger.info(
        "Stage 2 (GeckoTerminal): mapped %d of %d tokens (%d API calls)",
        len(gt_mapped), len(still_unmapped), gt_api_calls,
    )

    final_unmapped = [m for m in still_unmapped if m not in gt_mapped]

    logger.info("Unmapped after both stages: %d tokens", len(final_unmapped))
    if final_unmapped and len(final_unmapped) <= 20:
        logger.info("Unmapped addresses: %s", final_unmapped)

    return {
        'dexscreener_mapped': len(dex_mapped),
        'geckoterminal_mapped': len(gt_mapped),
        'unmapped': len(final_unmapped),
        'total_processed': len(mint_addresses),
        'api_calls': total_api_calls,
    }


class Command(BaseCommand):
    help = "Populate pool mappings using Dexscreener/GeckoTerminal fallback chain"

    def add_arguments(self, parser):
        parser.add_argument(
            '--coin', type=str, default=None,
            help='Single mint address (bypasses batch — for debugging)',
        )
        parser.add_argument(
            '--workers', type=int, default=1,
            help='Number of concurrent workers (default: 1 = serial)',
        )

    def handle(self, *args, **options):
        workers = options['workers']
        if options['coin']:
            result = run_fallback_chain([options['coin']], workers=workers)
        else:
            result = run_fallback_chain(workers=workers)

        self.stdout.write(
            f"Dexscreener: {result['dexscreener_mapped']} mapped, "
            f"GeckoTerminal: {result['geckoterminal_mapped']} mapped, "
            f"Unmapped: {result['unmapped']}"
        )
