"""Conformance function: Shyft raw transactions -> canonical RawTransaction dicts.

Pure function — no side effects, no DB writes, no API calls.
Classifies each transaction: successful trades are parsed into RawTransaction
records; failed transactions and those without BuyEvent/SellEvent are routed
to the skipped list with a reason code.
"""

import json
import logging
from decimal import Decimal

from warehouse.models import SkipReason, TradeType

from pipeline.conformance.utils import make_skipped, parse_iso_timestamp

logger = logging.getLogger(__name__)


def conform(raw_transactions, mint_address, pool_address):
    """Transform raw Shyft transaction list into RawTransaction + SkippedTransaction records.

    Args:
        raw_transactions: List of raw Shyft transaction dicts (from connector).
        mint_address: String mint address for FK resolution (coin_id).
        pool_address: String pool address for SkippedTransaction records.

    Returns:
        Tuple of (parsed_records, skipped_records):
        - parsed_records: List of dicts matching RawTransaction fields.
        - skipped_records: List of dicts matching SkippedTransaction fields.
    """
    parsed_records = []
    skipped_records = []

    for tx in raw_transactions:
        tx_signature = tx['signatures'][0]
        timestamp = parse_iso_timestamp(tx['timestamp'])

        # 1. Failed transactions go to skipped
        if tx.get('status') != 'Success':
            skipped_records.append(make_skipped(
                tx_signature, timestamp, mint_address, pool_address,
                tx, SkipReason.FAILED,
            ))
            continue

        # 2. Find first BuyEvent or SellEvent
        trade_event = _find_trade_event(tx.get('events', []), pool_address)
        if trade_event is None:
            skipped_records.append(make_skipped(
                tx_signature, timestamp, mint_address, pool_address,
                tx, SkipReason.NO_TRADE_EVENT,
            ))
            continue

        # 3. Parse the trade event
        try:
            record = _extract_record(
                tx, trade_event, tx_signature, timestamp, mint_address,
            )
            parsed_records.append(record)
        except (KeyError, ValueError, TypeError):
            logger.warning(
                'Parse error for tx %s: could not extract trade fields',
                tx_signature,
                exc_info=True,
            )
            skipped_records.append(make_skipped(
                tx_signature, timestamp, mint_address, pool_address,
                tx, SkipReason.PARSE_ERROR,
            ))

    return parsed_records, skipped_records


def _event_identity(event):
    """Return a stable identity for a trade event.

    Shyft occasionally emits duplicate BuyEvent/SellEvent payloads for the
    same transaction. Deduplicating them keeps warning noise focused on
    genuinely ambiguous transactions.
    """
    return json.dumps(
        {
            'name': event.get('name'),
            'data': event.get('data', {}),
        },
        sort_keys=True,
    )


def _dedupe_trade_events(events):
    """Drop duplicate trade events while preserving order."""
    seen = set()
    unique = []
    for event in events:
        identity = _event_identity(event)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(event)
    return unique


def _find_trade_event(events, pool_address):
    """Find the best BuyEvent/SellEvent candidate for the requested pool.

    Preference order:
      1. Unique trade event whose `data.pool` matches the requested pool.
      2. Unique trade event overall.
      3. First remaining trade event, with a warning because the transaction
         is ambiguous for the requested pool.
    """
    trade_events = _dedupe_trade_events([
        e for e in events if e.get('name') in ('BuyEvent', 'SellEvent')
    ])

    if not trade_events:
        return None

    matching_pool = [
        event for event in trade_events
        if event.get('data', {}).get('pool') == pool_address
    ]
    if len(matching_pool) == 1:
        return matching_pool[0]

    if len(trade_events) == 1:
        only_event = trade_events[0]
        if only_event.get('data', {}).get('pool') != pool_address:
            logger.warning(
                'Trade event pool mismatch for requested pool %s, using %s',
                pool_address,
                only_event.get('data', {}).get('pool'),
            )
        return only_event

    if len(matching_pool) > 1:
        logger.warning(
            'Multiple trade events found for requested pool %s (%d), taking the first',
            pool_address,
            len(matching_pool),
        )
        return matching_pool[0]

    logger.warning(
        'Multiple trade events found with no pool match for requested pool %s (%d), taking the first',
        pool_address,
        len(trade_events),
    )

    return trade_events[0]


def _extract_record(tx, trade_event, tx_signature, timestamp, mint_address):
    """Extract a RawTransaction dict from a transaction and its trade event."""
    event_name = trade_event['name']
    data = trade_event['data']

    is_buy = event_name == 'BuyEvent'

    return {
        'tx_signature': tx_signature,
        'timestamp': timestamp,
        'trade_type': TradeType.BUY if is_buy else TradeType.SELL,
        'wallet_address': data['user'],
        'token_amount': int(data['base_amount_out'] if is_buy else data['base_amount_in']),
        'sol_amount': int(data['quote_amount_in'] if is_buy else data['quote_amount_out']),
        'pool_address': data['pool'],
        'tx_fee': Decimal(str(float(tx['fee']))),
        'lp_fee': int(data['lp_fee']),
        'protocol_fee': int(data['protocol_fee']),
        'coin_creator_fee': int(data['coin_creator_fee']),
        'pool_token_reserves': int(data['pool_base_token_reserves']),
        'pool_sol_reserves': int(data['pool_quote_token_reserves']),
        'coin_id': mint_address,
    }

