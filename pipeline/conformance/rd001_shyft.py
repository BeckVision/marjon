"""Conformance function: Shyft raw transactions -> canonical RawTransaction dicts.

Pure function — no side effects, no DB writes, no API calls.
Classifies each transaction: successful trades are parsed into RawTransaction
records; failed transactions and those without BuyEvent/SellEvent are routed
to the skipped list with a reason code.
"""

import logging
from decimal import Decimal

from warehouse.models import SkipReason, TradeType

from pipeline.conformance.utils import parse_iso_timestamp

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
            skipped_records.append(_make_skipped(
                tx_signature, timestamp, mint_address, pool_address,
                tx, SkipReason.FAILED,
            ))
            continue

        # 2. Find first BuyEvent or SellEvent
        trade_event = _find_trade_event(tx.get('events', []))
        if trade_event is None:
            skipped_records.append(_make_skipped(
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
            skipped_records.append(_make_skipped(
                tx_signature, timestamp, mint_address, pool_address,
                tx, SkipReason.PARSE_ERROR,
            ))

    return parsed_records, skipped_records


def _find_trade_event(events):
    """Find the first BuyEvent or SellEvent in the events list.

    Returns the event dict, or None if no trade event found.
    Logs a warning if multiple trade events are present.
    """
    trade_events = [
        e for e in events if e.get('name') in ('BuyEvent', 'SellEvent')
    ]

    if not trade_events:
        return None

    if len(trade_events) > 1:
        logger.warning(
            'Multiple trade events found (%d), taking the first',
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


def _make_skipped(tx_signature, timestamp, mint_address, pool_address, tx, reason):
    """Build a SkippedTransaction dict."""
    return {
        'tx_signature': tx_signature,
        'timestamp': timestamp,
        'coin_id': mint_address,
        'pool_address': pool_address,
        'tx_type': tx.get('type', 'UNKNOWN'),
        'tx_status': tx.get('status', 'UNKNOWN'),
        'skip_reason': reason,
        'raw_json': tx,
    }
