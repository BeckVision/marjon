"""
Step handlers for the orchestrator.
Each handler runs one pipeline step for one coin (or one discovery batch).
They reuse existing connector/conformance/loader logic — no duplication.
"""

import logging

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

    Reuses fetch_ohlcv command logic.

    Returns:
        dict with 'status', 'records_loaded', 'api_calls', 'error_message'.
    """
    from pipeline.management.commands.fetch_ohlcv import (
        fetch_ohlcv_for_coin,
    )
    return fetch_ohlcv_for_coin(coin.mint_address)


def run_holders(coin, config):
    """Fetch holders for one coin.

    Placeholder — not yet wired up. Holders step is commented out in config.
    """
    raise NotImplementedError("Holder pipeline not yet wired into orchestrator")
