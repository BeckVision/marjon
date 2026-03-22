#!/usr/bin/env python3
"""Historical backfill via Helius for coins beyond Shyft's retention.

Processes coins in priority order (those with FL-001 data first).
Tracks credit usage and stops before exceeding budget.
"""

import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['DJANGO_SETTINGS_MODULE'] = 'marjon.settings'
django.setup()

from warehouse.models import (
    MigratedCoin, OHLCVCandle, PoolMapping, RawTransaction,
)
from pipeline.management.commands.fetch_transactions import (
    fetch_transactions_for_coin,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
)
logger = logging.getLogger('backfill')

# Budget
MONTHLY_BUDGET = 1_000_000
SAFETY_MARGIN = 50_000  # stop 50K credits before limit
MAX_CREDITS = MONTHLY_BUDGET - SAFETY_MARGIN

now = datetime.now(timezone.utc)

# Find coins needing backfill
coins_with_data = set(
    RawTransaction.objects.values_list('coin_id', flat=True).distinct()
)
old_coins = set(
    PoolMapping.objects.filter(
        coin__anchor_event__lte=now - timedelta(days=3),
    ).values_list('coin_id', flat=True)
)
need_backfill = old_coins - coins_with_data

# Priority: coins with FL-001 data first
coins_with_ohlcv = set(
    OHLCVCandle.objects.values_list('coin_id', flat=True).distinct()
)
priority = sorted(need_backfill & coins_with_ohlcv)
non_priority = sorted(need_backfill - coins_with_ohlcv)
queue = priority + non_priority

logger.info(
    "Backfill queue: %d coins (%d priority, %d non-priority)",
    len(queue), len(priority), len(non_priority),
)

# Process
credits_used = 0
processed = 0
failed = 0
skipped = 0

for i, mint in enumerate(queue):
    if credits_used >= MAX_CREDITS:
        logger.info(
            "Credit budget reached (%d/%d). Stopping.",
            credits_used, MAX_CREDITS,
        )
        break

    logger.info(
        "[%d/%d] Processing %s... (credits: %d/%d)",
        i + 1, len(queue), mint[:30], credits_used, MAX_CREDITS,
    )

    try:
        result = fetch_transactions_for_coin(mint, source='helius')
        api_calls = result.get('api_calls', 0)
        # Estimate credits: RPC calls * 10 + REST calls * 100
        # api_calls includes both, approximate as all enhanced
        est_credits = api_calls * 100
        credits_used += est_credits
        processed += 1

        logger.info(
            "  -> %d loaded, %d skipped, %d calls (~%d credits). "
            "Total credits: %d",
            result['records_loaded'], result['records_skipped'],
            api_calls, est_credits, credits_used,
        )
    except Exception as e:
        failed += 1
        logger.error("  -> FAILED: %s", e)

    # Brief pause between coins
    time.sleep(0.5)

logger.info(
    "Backfill complete: %d processed, %d failed, %d remaining. "
    "Credits used: ~%d",
    processed, failed, len(queue) - processed - failed, credits_used,
)
