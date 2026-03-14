"""Management command to populate pool mappings via Dexscreener/GeckoTerminal fallback chain."""

import logging
import time

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


def run_fallback_chain(mint_addresses=None):
    """Execute the Dexscreener -> GeckoTerminal fallback chain.

    Args:
        mint_addresses: List of mint addresses to process.
            If None, queries all unmapped tokens.

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
    for batch in batches:
        raw_pairs, meta = dex_fetch(batch)
        total_api_calls += meta['api_calls']
        canonical = dex_conform(raw_pairs)
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
        for idx, batch in enumerate(gt_batches):
            raw_response, meta = gt_fetch(batch)
            gt_api_calls += meta['api_calls']
            canonical = gt_conform(raw_response)
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

    def handle(self, *args, **options):
        if options['coin']:
            result = run_fallback_chain([options['coin']])
        else:
            result = run_fallback_chain()

        self.stdout.write(
            f"Dexscreener: {result['dexscreener_mapped']} mapped, "
            f"GeckoTerminal: {result['geckoterminal_mapped']} mapped, "
            f"Unmapped: {result['unmapped']}"
        )
