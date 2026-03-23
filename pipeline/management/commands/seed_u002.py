"""Seed the U-002 universe with the fixed entity list.

Usage:
    python manage.py seed_u002
"""

from django.core.management.base import BaseCommand

from warehouse.models import BinanceAsset

ASSETS = [
    {'symbol': 'BTCUSDT', 'base_asset': 'BTC', 'quote_asset': 'USDT'},
    {'symbol': 'ETHUSDT', 'base_asset': 'ETH', 'quote_asset': 'USDT'},
    {'symbol': 'SOLUSDT', 'base_asset': 'SOL', 'quote_asset': 'USDT'},
]


class Command(BaseCommand):
    help = "Seed U-002 universe with BTCUSDT, ETHUSDT, SOLUSDT"

    def handle(self, *args, **options):
        created = 0
        for asset_data in ASSETS:
            _, was_created = BinanceAsset.objects.update_or_create(
                symbol=asset_data['symbol'],
                defaults={
                    'base_asset': asset_data['base_asset'],
                    'quote_asset': asset_data['quote_asset'],
                },
            )
            if was_created:
                created += 1
                self.stdout.write(f"Created {asset_data['symbol']}")
            else:
                self.stdout.write(f"Already exists: {asset_data['symbol']}")

        self.stdout.write(
            f"\nDone: {created} created, {len(ASSETS) - created} already existed"
        )
