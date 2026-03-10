"""Conformance function: Moralis raw JSON -> canonical HolderSnapshot dicts.

Pure function — no side effects, no DB writes, no API calls.
"""

from datetime import datetime
from decimal import Decimal


def conform(raw_response, mint_address):
    """Transform raw Moralis holder response to canonical form.

    Args:
        raw_response: List of dicts from Moralis API (ascending order).
        mint_address: String mint address for FK resolution.

    Returns:
        List of dicts matching HolderSnapshot field names.
        Note: ingested_at is handled by the model's auto_now_add=True.
    """
    records = []

    for raw in raw_response:
        ts_str = raw['timestamp']
        # Strip .000Z milliseconds
        if ts_str.endswith('.000Z'):
            ts_str = ts_str[:-5] + '+00:00'
        elif ts_str.endswith('Z'):
            ts_str = ts_str[:-1] + '+00:00'
        ts = datetime.fromisoformat(ts_str)

        holders_in = raw.get('holdersIn', {})
        holders_out = raw.get('holdersOut', {})
        acquisition = raw.get('newHoldersByAcquisition', {})

        records.append({
            'timestamp': ts,
            'coin_id': mint_address,
            'total_holders': raw.get('totalHolders'),
            'net_holder_change': raw.get('netHolderChange'),
            'holder_percent_change': _to_decimal(
                raw.get('holderPercentChange')
            ),
            'acquired_via_swap': acquisition.get('swap'),
            'acquired_via_transfer': acquisition.get('transfer'),
            'acquired_via_airdrop': acquisition.get('airdrop'),
            'holders_in_whales': holders_in.get('whales'),
            'holders_in_sharks': holders_in.get('sharks'),
            'holders_in_dolphins': holders_in.get('dolphins'),
            'holders_in_fish': holders_in.get('fish'),
            'holders_in_octopus': holders_in.get('octopus'),
            'holders_in_crabs': holders_in.get('crabs'),
            'holders_in_shrimps': holders_in.get('shrimps'),
            'holders_out_whales': holders_out.get('whales'),
            'holders_out_sharks': holders_out.get('sharks'),
            'holders_out_dolphins': holders_out.get('dolphins'),
            'holders_out_fish': holders_out.get('fish'),
            'holders_out_octopus': holders_out.get('octopus'),
            'holders_out_crabs': holders_out.get('crabs'),
            'holders_out_shrimps': holders_out.get('shrimps'),
        })

    return records


def _to_decimal(value):
    """Convert a value to Decimal, or None if None."""
    if value is None:
        return None
    return Decimal(str(value))
