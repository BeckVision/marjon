"""Loader for U-001: upsert into MigratedCoin table."""

import logging

from warehouse.models import MigratedCoin

logger = logging.getLogger(__name__)


def load_graduated_tokens(canonical_tokens):
    """Upsert canonical graduated tokens into MigratedCoin.

    Uses update_or_create with create_defaults so that anchor_event
    is set ONLY on create — never overwritten on re-encounter.

    Args:
        canonical_tokens: List of dicts from conformance.

    Returns:
        Tuple of (created_count, updated_count).
    """
    created_count = 0
    updated_count = 0

    for token in canonical_tokens:
        _, created = MigratedCoin.objects.update_or_create(
            mint_address=token['mint_address'],
            defaults={
                'name': token['name'],
                'symbol': token['symbol'],
                'decimals': token['decimals'],
                'logo_url': token['logo_url'],
            },
            create_defaults={
                'name': token['name'],
                'symbol': token['symbol'],
                'decimals': token['decimals'],
                'logo_url': token['logo_url'],
                'anchor_event': token['anchor_event'],
            },
        )
        if created:
            created_count += 1
        else:
            updated_count += 1

    logger.info(
        "Loaded %d tokens (created=%d, updated=%d)",
        len(canonical_tokens), created_count, updated_count,
    )
    return created_count, updated_count
