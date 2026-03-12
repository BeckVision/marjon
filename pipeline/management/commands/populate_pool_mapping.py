"""Management command to populate pool mapping for a token."""

import logging
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pipeline.connectors.dexpaprika import fetch_token_pools
from warehouse.models import MigratedCoin, PoolMapping

logger = logging.getLogger(__name__)


def populate_pool_mapping_for_coin(mint_address):
    """Core pool mapping logic for one coin.

    Args:
        mint_address: Token mint address.

    Returns:
        dict with 'status' ('created', 'exists', 'no_pumpswap_pool', 'error'),
        'pools_created', 'pools_updated', 'error_message'.

    Raises:
        ValueError: If coin doesn't exist.
        RuntimeError: If API call fails.
    """
    if not MigratedCoin.objects.filter(mint_address=mint_address).exists():
        raise ValueError(
            f"MigratedCoin with mint_address={mint_address} does not exist"
        )

    # Check if mapping already exists
    if PoolMapping.objects.filter(coin_id=mint_address).exists():
        return {
            'status': 'exists',
            'pools_created': 0,
            'pools_updated': 0,
            'error_message': None,
        }

    logger.info("Fetching pools for %s...", mint_address)
    try:
        pools = fetch_token_pools(mint_address)
    except Exception as e:
        raise RuntimeError(
            f"DexPaprika pool fetch failed for {mint_address}"
        ) from e

    if not pools:
        return {
            'status': 'no_pools',
            'pools_created': 0,
            'pools_updated': 0,
            'error_message': f"No pools found for {mint_address}",
        }

    # Filter for Pumpswap pools
    pumpswap_pools = [
        p for p in pools
        if p.get('dex_id') == 'pumpswap'
        or p.get('dexId') == 'pumpswap'
    ]

    if not pumpswap_pools:
        return {
            'status': 'no_pumpswap_pool',
            'pools_created': 0,
            'pools_updated': 0,
            'error_message': (
                f"No Pumpswap pools found. Available DEXes: "
                f"{set(p.get('dex_id', p.get('dexId', '?')) for p in pools)}"
            ),
        }

    created_count = 0
    updated_count = 0

    for pool in pumpswap_pools:
        pool_addr = pool.get('id') or pool.get('address', '')

        if not pool_addr:
            logger.warning(
                "Skipping pool with empty address for %s: %s",
                mint_address, pool,
            )
            continue

        created = pool.get('created_at')
        created_dt = None
        if created:
            if isinstance(created, str):
                if created.endswith('Z'):
                    created = created[:-1] + '+00:00'
                created_dt = datetime.fromisoformat(created)

        _, created_flag = PoolMapping.objects.update_or_create(
            coin_id=mint_address,
            pool_address=pool_addr,
            defaults={
                'dex': 'pumpswap',
                'source': 'dexpaprika',
                'created_at': created_dt,
            },
        )

        if created_flag:
            created_count += 1
        else:
            updated_count += 1

        action = "Created" if created_flag else "Updated"
        logger.info("%s PoolMapping: %s -> %s", action, mint_address, pool_addr)

    return {
        'status': 'created' if created_count else 'updated',
        'pools_created': created_count,
        'pools_updated': updated_count,
        'error_message': None,
    }


class Command(BaseCommand):
    help = "Discover Pumpswap pool for a token and save to PoolMapping"

    def add_arguments(self, parser):
        parser.add_argument('--coin', required=True, help='Mint address')

    def handle(self, *args, **options):
        mint = options['coin']

        try:
            result = populate_pool_mapping_for_coin(mint)
        except (ValueError, RuntimeError) as e:
            raise CommandError(str(e))

        if result['status'] == 'exists':
            self.stdout.write(f"PoolMapping already exists for {mint}")
        elif result['status'] in ('no_pools', 'no_pumpswap_pool'):
            raise CommandError(result['error_message'])
        else:
            self.stdout.write(
                f"{result['status'].title()}: "
                f"{result['pools_created']} created, "
                f"{result['pools_updated']} updated"
            )
