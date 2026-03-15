"""Conformance function: Helius enhanced transactions -> canonical RawTransaction dicts.

Pure function — no side effects, no DB writes, no API calls.
Extracts trade data from tokenTransfers (not events.swap, which is empty for
~80% of PUMP_AMM transactions). Fee breakdown derived from wrapped SOL transfers.

Pool reserves (pool_token_reserves, pool_sol_reserves) are set to None —
they are not available from Helius tokenTransfers. Would require decoding
the PUMP_AMM inner instruction Anchor event log.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal

from warehouse.models import SkipReason, TradeType

from pipeline.conformance.utils import make_skipped

logger = logging.getLogger(__name__)

WSOL_MINT = "So11111111111111111111111111111111111111112"

# Pump.fun AMM fee basis points (fixed)
LP_FEE_BPS = 2


def conform(raw_transactions, mint_address, pool_address):
    """Transform Helius EnhancedTransaction list into RawTransaction + SkippedTransaction records.

    Args:
        raw_transactions: List of Helius EnhancedTransaction dicts.
        mint_address: String mint address for FK resolution (coin_id).
        pool_address: String pool address for trade direction detection.

    Returns:
        Tuple of (parsed_records, skipped_records).
    """
    parsed_records = []
    skipped_records = []

    for tx in raw_transactions:
        tx_signature = tx.get('signature', '')
        timestamp = datetime.fromtimestamp(
            tx.get('timestamp', 0), tz=timezone.utc,
        )

        # 1. Failed transactions
        if tx.get('transactionError') is not None:
            skipped_records.append(make_skipped(
                tx_signature, timestamp, mint_address, pool_address,
                tx, SkipReason.FAILED,
                tx_status='Fail',
            ))
            continue

        # 2. Detect trade direction from tokenTransfers
        direction = _detect_trade_direction(tx, pool_address)
        if direction is None:
            skipped_records.append(make_skipped(
                tx_signature, timestamp, mint_address, pool_address,
                tx, SkipReason.NO_TRADE_EVENT,
                tx_status='Success',
            ))
            continue

        # 3. Extract trade record
        try:
            record = _extract_record(
                tx, direction, tx_signature, timestamp,
                mint_address, pool_address,
            )
            parsed_records.append(record)
        except (KeyError, ValueError, TypeError, IndexError):
            logger.warning(
                'Parse error for tx %s: could not extract trade fields',
                tx_signature,
                exc_info=True,
            )
            skipped_records.append(make_skipped(
                tx_signature, timestamp, mint_address, pool_address,
                tx, SkipReason.PARSE_ERROR,
                tx_status='Success',
            ))

    return parsed_records, skipped_records


def _detect_trade_direction(tx, pool_address):
    """Detect BUY or SELL from non-wSOL tokenTransfer involving the pool.

    BUY: non-wSOL token flows FROM pool to trader (pool sends tokens out).
    SELL: non-wSOL token flows TO pool from trader (pool receives tokens).

    Returns 'BUY', 'SELL', or None if no trade detected.
    """
    for tt in tx.get('tokenTransfers', []):
        if tt.get('mint') == WSOL_MINT:
            continue
        if tt.get('fromUserAccount') == pool_address:
            return 'BUY'
        if tt.get('toUserAccount') == pool_address:
            return 'SELL'
    return None


def _extract_record(tx, direction, tx_signature, timestamp,
                    mint_address, pool_address):
    """Extract a RawTransaction dict from a Helius transaction."""
    token_transfers = tx.get('tokenTransfers', [])
    trader = tx['feePayer']

    # Separate wSOL and token transfers involving the pool
    wsol_to_pool = []
    wsol_from_pool = []
    wsol_fees = []  # wSOL to/from non-pool non-trader addresses
    token_transfer = None

    for tt in token_transfers:
        mint = tt.get('mint', '')
        from_acc = tt.get('fromUserAccount', '')
        to_acc = tt.get('toUserAccount', '')
        amount_float = tt.get('tokenAmount', 0) or 0

        if mint == WSOL_MINT:
            if to_acc == pool_address:
                wsol_to_pool.append((to_acc, amount_float))
            elif from_acc == pool_address:
                if to_acc == trader:
                    wsol_from_pool.append(('trader', amount_float))
                else:
                    wsol_fees.append((to_acc, amount_float))
            elif from_acc == trader and to_acc != pool_address:
                # BUY: trader sends wSOL fees to protocol/creator
                wsol_fees.append((to_acc, amount_float))
        else:
            # Non-wSOL token transfer involving pool
            if from_acc == pool_address or to_acc == pool_address:
                token_transfer = tt

    if token_transfer is None:
        raise ValueError(f"No token transfer involving pool for {tx_signature}")

    # Token amount: get raw integer from accountData if available
    token_amount = _get_raw_token_amount(tx, token_transfer, pool_address, direction)

    # Fee extraction: sort wSOL fee transfers by amount (descending)
    wsol_fees.sort(key=lambda x: x[1], reverse=True)
    protocol_fee = _sol_to_lamports(wsol_fees[0][1]) if len(wsol_fees) >= 1 else 0
    coin_creator_fee = _sol_to_lamports(wsol_fees[1][1]) if len(wsol_fees) >= 2 else 0

    # SOL amount and LP fee computation
    if direction == 'BUY':
        # wSOL to pool = quote_amount_in + lp_fee (gross to pool)
        wsol_to_pool_total = sum(_sol_to_lamports(a) for _, a in wsol_to_pool)
        net_sol = wsol_to_pool_total * 10000 // (10000 + LP_FEE_BPS)
        lp_fee = wsol_to_pool_total - net_sol
        sol_amount = net_sol
    else:
        # SELL: visible_sum = all wSOL leaving pool (trader + fees)
        visible_sum = (
            sum(_sol_to_lamports(a) for _, a in wsol_from_pool)
            + protocol_fee + coin_creator_fee
        )
        gross = visible_sum * 10000 // (10000 - LP_FEE_BPS)
        lp_fee = gross - visible_sum
        sol_amount = gross

    # tx_fee: Helius provides lamports int, convert to Decimal SOL
    tx_fee = Decimal(tx.get('fee', 0)) / Decimal(10 ** 9)

    return {
        'tx_signature': tx_signature,
        'timestamp': timestamp,
        'trade_type': TradeType.BUY if direction == 'BUY' else TradeType.SELL,
        'wallet_address': trader,
        'token_amount': token_amount,
        'sol_amount': sol_amount,
        'pool_address': pool_address,
        'tx_fee': tx_fee,
        'lp_fee': lp_fee,
        'protocol_fee': protocol_fee,
        'coin_creator_fee': coin_creator_fee,
        'pool_token_reserves': None,
        'pool_sol_reserves': None,
        'coin_id': mint_address,
    }


def _get_raw_token_amount(tx, token_transfer, pool_address, direction):
    """Get exact integer token amount from accountData or tokenTransfers.

    Prefers accountData.tokenBalanceChanges.rawTokenAmount (string integer)
    for precision. Falls back to tokenTransfers float × 10^decimals.
    Single pass through accountData to find both amount and decimals.
    """
    mint = token_transfer.get('mint', '')
    decimals = 6  # default for pump.fun tokens

    # Single scan of accountData for matching mint
    for ad in tx.get('accountData', []):
        for tbc in ad.get('tokenBalanceChanges', []):
            if tbc.get('mint') != mint:
                continue
            raw = tbc.get('rawTokenAmount', {})
            decimals = raw.get('decimals', 6)
            token_amount_str = raw.get('tokenAmount')
            if token_amount_str is not None:
                amount = int(token_amount_str)
                if amount > 0:
                    return amount

    # Fallback: float conversion using decimals found above
    amount_float = token_transfer.get('tokenAmount', 0) or 0
    return round(amount_float * (10 ** decimals))


def _sol_to_lamports(sol_amount):
    """Convert SOL float to lamports integer."""
    return round(sol_amount * 10 ** 9)


