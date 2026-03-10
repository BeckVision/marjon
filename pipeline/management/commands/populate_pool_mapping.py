"""Management command to populate pool mapping for a token."""

import logging
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pipeline.connectors.dexpaprika import fetch_token_pools
from warehouse.models import MigratedCoin, PoolMapping

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Discover Pumpswap pool for a token and save to PoolMapping"

    def add_arguments(self, parser):
        parser.add_argument('--coin', required=True, help='Mint address')

    def handle(self, *args, **options):
        mint = options['coin']

        if not MigratedCoin.objects.filter(mint_address=mint).exists():
            raise CommandError(
                f"MigratedCoin with mint_address={mint} does not exist"
            )

        logger.info("Fetching pools for %s...", mint)
        try:
            pools = fetch_token_pools(mint)
        except Exception:
            logger.error(
                "Failed to fetch pools for %s", mint, exc_info=True,
            )
            raise CommandError(f"DexPaprika pool fetch failed for {mint}")

        if not pools:
            raise CommandError(f"No pools found for {mint}")

        # Filter for Pumpswap pools
        pumpswap_pools = [
            p for p in pools
            if p.get('dex_id') == 'pumpswap'
            or p.get('dexId') == 'pumpswap'
        ]

        if not pumpswap_pools:
            raise CommandError(
                f"No Pumpswap pools found. Available DEXes: "
                f"{set(p.get('dex_id', p.get('dexId', '?')) for p in pools)}"
            )

        for pool in pumpswap_pools:
            pool_addr = pool.get('id') or pool.get('address', '')

            if not pool_addr:
                logger.warning(
                    "Skipping pool with empty address for %s: %s",
                    mint, pool,
                )
                continue

            created = pool.get('created_at')
            created_dt = None
            if created:
                if isinstance(created, str):
                    if created.endswith('Z'):
                        created = created[:-1] + '+00:00'
                    created_dt = datetime.fromisoformat(created)

            obj, created_flag = PoolMapping.objects.update_or_create(
                coin_id=mint,
                pool_address=pool_addr,
                defaults={
                    'dex': 'pumpswap',
                    'source': 'dexpaprika',
                    'created_at': created_dt,
                },
            )

            action = "Created" if created_flag else "Updated"
            logger.info(
                "%s PoolMapping: %s -> %s", action, mint, pool_addr,
            )
            self.stdout.write(
                f"{action} PoolMapping: {mint} -> {pool_addr}"
            )
