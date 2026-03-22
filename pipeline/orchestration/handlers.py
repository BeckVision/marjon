"""
Step handlers for the orchestrator.
Each handler runs one pipeline step for one coin (or one discovery batch).
They reuse existing connector/conformance/loader logic — no duplication.
"""

import logging

from pipeline.runner import run_for_coin

logger = logging.getLogger(__name__)


def run_discovery_u001(config, days=None, max_pages=None):
    """Run universe discovery. Not per-coin — discovers all new tokens.

    Reuses discover_graduates command logic.

    Returns:
        dict with 'created', 'updated', 'cu_consumed', 'pages'.
    """
    from pipeline.management.commands.discover_graduates import (
        run_discovery_steady_state,
    )
    return run_discovery_steady_state(max_pages=max_pages)


def run_pool_mapping(coins, config):
    """Populate pool mappings for a list of coins using the fallback chain.

    Not per-coin — runs batch discovery for all unmapped coins at once.

    Args:
        coins: List of MigratedCoin instances (unmapped ones).
        config: Universe config dict (unused but kept for handler contract).

    Returns:
        dict with 'dexscreener_mapped', 'geckoterminal_mapped',
        'unmapped', 'total_processed', 'api_calls'.
    """
    from pipeline.management.commands.populate_pool_mapping import (
        run_fallback_chain,
    )
    mint_addresses = [c.mint_address for c in coins]
    return run_fallback_chain(mint_addresses)


def run_ohlcv(coin, config):
    """Fetch OHLCV for one coin.

    Returns:
        dict with 'status', 'records_loaded', 'api_calls', 'error_message'.
    """
    from pipeline.pipelines.fl001 import FL001
    return run_for_coin(FL001, coin.mint_address)


def run_raw_transactions(coin, config):
    """Fetch raw transactions for one coin.

    Returns:
        dict with 'status', 'records_loaded', 'records_skipped',
        'api_calls', 'error_message'.
    """
    from pipeline.pipelines.rd001 import RD001
    source = config.get('source', 'auto')
    parse_workers = config.get('parse_workers', 1)
    return run_for_coin(
        RD001, coin.mint_address, source=source, parse_workers=parse_workers,
    )


def run_holders(coin, config):
    """Fetch holders for one coin.

    Returns:
        dict with 'status', 'records_loaded', 'api_calls', 'error_message'.
    """
    from pipeline.pipelines.fl002 import FL002
    return run_for_coin(FL002, coin.mint_address)
