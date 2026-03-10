"""Management command to discover graduated pump.fun tokens."""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max

from pipeline.conformance.u001_universe_moralis import conform_moralis_graduated
from pipeline.connectors.moralis import (
    CU_PER_CALL,
    DAILY_CU_LIMIT,
    get_daily_cu_used,
)
from pipeline.connectors.moralis_discovery import fetch_graduated_tokens
from pipeline.loaders.u001_universe import load_graduated_tokens
from warehouse.models import MigratedCoin, PipelineBatchRun, RunMode, RunStatus

logger = logging.getLogger(__name__)

BOOTSTRAP_STATE_PATH = Path(__file__).resolve().parent.parent.parent.parent / '.moralis_bootstrap_state.json'

DEFAULT_MAX_PAGES_STEADY = 50
DEFAULT_MAX_PAGES_BOOTSTRAP = 700


class Command(BaseCommand):
    help = "Discover graduated pump.fun tokens via Moralis and load into MigratedCoin"

    def add_arguments(self, parser):
        parser.add_argument(
            '--mode', required=True, choices=['bootstrap', 'steady-state'],
            help='bootstrap: paginate all tokens; steady-state: stop at watermark',
        )
        parser.add_argument(
            '--max-pages', type=int, default=None,
            help='Safety limit on pages (default: 50 steady-state, 700 bootstrap)',
        )
        parser.add_argument(
            '--restart-bootstrap', action='store_true',
            help='Delete saved bootstrap state and start from scratch',
        )

    def handle(self, *args, **options):
        mode = options['mode']
        max_pages = options['max_pages']
        restart_bootstrap = options['restart_bootstrap']

        if max_pages is None:
            max_pages = (
                DEFAULT_MAX_PAGES_BOOTSTRAP if mode == 'bootstrap'
                else DEFAULT_MAX_PAGES_STEADY
            )

        # CU budget check
        daily_used = get_daily_cu_used()
        if daily_used + CU_PER_CALL > DAILY_CU_LIMIT:
            raise CommandError(
                f"Insufficient CU budget. Used={daily_used}, "
                f"limit={DAILY_CU_LIMIT}, need at least {CU_PER_CALL} for one page."
            )

        # Create batch run
        batch = PipelineBatchRun.objects.create(
            pipeline_id='universe',
            mode=RunMode.BOOTSTRAP if mode == 'bootstrap' else RunMode.STEADY_STATE,
            status=RunStatus.STARTED,
            started_at=datetime.now(timezone.utc),
        )

        try:
            if mode == 'bootstrap':
                self._handle_bootstrap(batch, max_pages, restart_bootstrap)
            else:
                self._handle_steady_state(batch, max_pages)
        except CommandError:
            batch.status = RunStatus.ERROR
            batch.completed_at = datetime.now(timezone.utc)
            batch.save()
            raise
        except Exception as e:
            batch.status = RunStatus.ERROR
            batch.error_message = str(e)
            batch.completed_at = datetime.now(timezone.utc)
            batch.save()
            logger.error("Discovery failed", exc_info=True)
            raise CommandError(f"Discovery failed: {e}")

    def _handle_steady_state(self, batch, max_pages):
        """Fetch newest graduates, stop when we hit known tokens."""
        watermark = MigratedCoin.objects.aggregate(
            Max('anchor_event')
        )['anchor_event__max']

        if watermark is None:
            raise CommandError(
                "No existing tokens. Run bootstrap first."
            )

        logger.info("Steady-state mode: watermark=%s", watermark)

        tokens_to_load = []
        cursor = None
        pages_fetched = 0
        cu_consumed = 0
        hit_watermark = False

        for page_num in range(1, max_pages + 1):
            # CU budget check before each page
            daily_used = get_daily_cu_used()
            if daily_used + CU_PER_CALL > DAILY_CU_LIMIT:
                logger.warning(
                    "CU budget exhausted after %d pages. Stopping.",
                    pages_fetched,
                )
                break

            data = fetch_graduated_tokens(cursor=cursor)
            pages_fetched += 1
            cu_consumed += CU_PER_CALL

            result = data.get('result', [])
            if not result:
                break

            for token in result:
                graduated_at_str = token['graduatedAt']
                # Quick parse for comparison
                if graduated_at_str.endswith('.000Z'):
                    ts_str = graduated_at_str[:-5] + '+00:00'
                elif graduated_at_str.endswith('Z'):
                    ts_str = graduated_at_str[:-1] + '+00:00'
                else:
                    ts_str = graduated_at_str
                graduated_at = datetime.fromisoformat(ts_str)

                if graduated_at < watermark:
                    hit_watermark = True
                    break

                tokens_to_load.append(token)

            if hit_watermark:
                break

            cursor = data.get('cursor')
            if not cursor:
                break

            time.sleep(0.5)

        # Process collected tokens
        created = 0
        updated = 0
        if tokens_to_load:
            canonical = conform_moralis_graduated(tokens_to_load)
            created, updated = load_graduated_tokens(canonical)

            # Task 6 Option A: trigger pool mapping for newly created tokens
            # TODO: Option B — dispatch as Celery task per new token
            self._populate_pools_for_new_tokens(canonical, created)

        # Update batch
        batch.status = RunStatus.COMPLETE
        batch.completed_at = datetime.now(timezone.utc)
        batch.coins_attempted = len(tokens_to_load)
        batch.coins_succeeded = created + updated
        batch.cu_consumed = cu_consumed
        batch.api_calls = pages_fetched
        batch.save()

        logger.info(
            "Steady-state complete: %d new, %d updated, %d pages, %d CU",
            created, updated, pages_fetched, cu_consumed,
        )
        self.stdout.write(
            f"Discovered {created} new tokens, updated {updated}, "
            f"{pages_fetched} pages, {cu_consumed} CU consumed"
        )

    def _handle_bootstrap(self, batch, max_pages, restart_bootstrap):
        """Paginate through all graduated tokens."""
        if restart_bootstrap and BOOTSTRAP_STATE_PATH.exists():
            BOOTSTRAP_STATE_PATH.unlink()
            logger.info("Deleted bootstrap state file, starting fresh")

        # Resume from saved state
        cursor = None
        pages_completed = 0
        if BOOTSTRAP_STATE_PATH.exists():
            state = json.loads(BOOTSTRAP_STATE_PATH.read_text())
            cursor = state.get('cursor')
            pages_completed = state.get('pages_completed', 0)
            logger.info(
                "Resuming bootstrap from page %d", pages_completed + 1,
            )
        else:
            # Check if table has data and restart not requested
            if MigratedCoin.objects.exists() and not restart_bootstrap:
                raise CommandError(
                    "MigratedCoin table has data. Use --restart-bootstrap "
                    "to re-bootstrap or steady-state to update."
                )

        total_created = 0
        total_updated = 0
        cu_consumed = 0

        for page_num in range(1, max_pages + 1):
            # CU budget check before each page
            daily_used = get_daily_cu_used()
            if daily_used + CU_PER_CALL > DAILY_CU_LIMIT:
                logger.warning(
                    "CU budget exhausted after %d pages. "
                    "Resume tomorrow.",
                    pages_completed,
                )
                break

            data = fetch_graduated_tokens(cursor=cursor)
            cu_consumed += CU_PER_CALL

            result = data.get('result', [])
            if not result:
                break

            # Connector -> Conformance -> Loader per page
            canonical = conform_moralis_graduated(result)
            created, updated = load_graduated_tokens(canonical)
            total_created += created
            total_updated += updated

            pages_completed += 1
            cursor = data.get('cursor')

            # Save state after each page
            BOOTSTRAP_STATE_PATH.write_text(json.dumps({
                'cursor': cursor,
                'pages_completed': pages_completed,
                'cu_used': cu_consumed,
                'last_updated': datetime.now(timezone.utc).isoformat(),
            }))

            if not cursor:
                # All pages exhausted
                logger.info(
                    "Bootstrap complete: %d tokens loaded (%d created, "
                    "%d updated) in %d pages",
                    total_created + total_updated, total_created,
                    total_updated, pages_completed,
                )
                BOOTSTRAP_STATE_PATH.unlink(missing_ok=True)
                break

            time.sleep(0.5)
        else:
            # max_pages reached before cursor is null
            logger.info(
                "Bootstrap incomplete: %d pages done, resume tomorrow. "
                "Created=%d, updated=%d",
                pages_completed, total_created, total_updated,
            )

        # Update batch
        batch.status = RunStatus.COMPLETE
        batch.completed_at = datetime.now(timezone.utc)
        batch.coins_attempted = total_created + total_updated
        batch.coins_succeeded = total_created + total_updated
        batch.cu_consumed = cu_consumed
        batch.api_calls = pages_completed
        batch.save()

        self.stdout.write(
            f"Bootstrap: {total_created} created, {total_updated} updated, "
            f"{pages_completed} pages, {cu_consumed} CU consumed"
        )

    def _populate_pools_for_new_tokens(self, canonical_tokens, created_count):
        """Trigger pool mapping for newly created tokens (Option A: synchronous).

        TODO: Option B — dispatch populate_pool_mapping as a Celery task
        per new token for async processing.
        """
        if created_count == 0:
            return

        from pipeline.connectors.dexpaprika import fetch_token_pools
        from warehouse.models import PoolMapping

        # Only process tokens that were actually created (not updated).
        # We check which mint_addresses were just created by looking at
        # tokens with no pool mappings yet.
        for token in canonical_tokens:
            mint = token['mint_address']
            if PoolMapping.objects.filter(coin_id=mint).exists():
                continue

            logger.info("Populating pool mapping for new token %s", mint)
            try:
                pools = fetch_token_pools(mint)
            except Exception:
                logger.warning(
                    "Failed to fetch pools for %s, skipping",
                    mint, exc_info=True,
                )
                continue

            if not pools:
                logger.warning("No pools found for %s", mint)
                continue

            pumpswap_pools = [
                p for p in pools
                if p.get('dex_id') == 'pumpswap'
                or p.get('dexId') == 'pumpswap'
            ]

            for pool in pumpswap_pools:
                pool_addr = pool.get('id') or pool.get('address', '')
                if not pool_addr:
                    continue

                created_at_raw = pool.get('created_at')
                created_dt = None
                if created_at_raw and isinstance(created_at_raw, str):
                    if created_at_raw.endswith('Z'):
                        created_at_raw = created_at_raw[:-1] + '+00:00'
                    created_dt = datetime.fromisoformat(created_at_raw)

                PoolMapping.objects.update_or_create(
                    coin_id=mint,
                    pool_address=pool_addr,
                    defaults={
                        'dex': 'pumpswap',
                        'source': 'dexpaprika',
                        'created_at': created_dt,
                    },
                )
                logger.info("Created PoolMapping: %s -> %s", mint, pool_addr)
