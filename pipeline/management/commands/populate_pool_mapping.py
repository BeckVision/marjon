"""Management command to populate pool mapping for a token."""

from django.core.management.base import BaseCommand
from django.utils import timezone

from pipeline.connectors.dexpaprika import fetch_token_pools
from warehouse.models import MigratedCoin, PoolMapping


class Command(BaseCommand):
    help = "Discover Pumpswap pool for a token and save to PoolMapping"

    def add_arguments(self, parser):
        parser.add_argument('--coin', required=True, help='Mint address')

    def handle(self, *args, **options):
        mint = options['coin']

        if not MigratedCoin.objects.filter(mint_address=mint).exists():
            self.stderr.write(
                f"MigratedCoin with mint_address={mint} does not exist"
            )
            return

        self.stdout.write(f"Fetching pools for {mint}...")
        pools = fetch_token_pools(mint)

        if not pools:
            self.stderr.write("No pools found")
            return

        # Filter for Pumpswap pools
        pumpswap_pools = [
            p for p in pools
            if p.get('dex_id') == 'pumpswap'
            or p.get('dexId') == 'pumpswap'
        ]

        if not pumpswap_pools:
            self.stderr.write(
                f"No Pumpswap pools found. Available DEXes: "
                f"{set(p.get('dex_id', p.get('dexId', '?')) for p in pools)}"
            )
            return

        for pool in pumpswap_pools:
            pool_addr = pool.get('id') or pool.get('address', '')
            created = pool.get('created_at')

            from datetime import datetime
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
            self.stdout.write(
                f"{action} PoolMapping: {mint} -> {pool_addr}"
            )
