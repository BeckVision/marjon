"""BacktestRunner — time-stepping engine.

Pre-loads all data via get_panel_slice() once, then iterates through
timesteps in memory. PIT semantics are enforced by the data service;
the runner only sees data that was visible by data_end.
"""

import logging
from collections import defaultdict
from decimal import Decimal

from data_service.operations import get_panel_slice

from .entity_state import EntityStateTracker
from .metrics import compute_metrics
from .positions import PositionTracker

# Import signal registry — importing u001 module triggers registration
from strategy.signals.registry import SIGNAL_REGISTRY
import strategy.signals.u001  # noqa: F401 — registers SG-001/002/003

logger = logging.getLogger(__name__)


class BacktestRunner:

    def __init__(self, strategy_config, asset_ids, data_start, data_end,
                 panel=None):
        self.config = strategy_config
        self.asset_ids = asset_ids
        self.data_start = data_start
        self.data_end = data_end
        self._preloaded_panel = panel

        self.price_field = self.config['price_field']
        exit_rules = self.config['exit_rules']
        self.take_profit_pct = Decimal(str(exit_rules['take_profit_pct']))
        self.stop_loss_pct = Decimal(str(exit_rules['stop_loss_pct']))
        self.max_hold_minutes = exit_rules['max_hold_minutes']

        sizing = self.config['position_sizing']
        self.amount_per_trade = Decimal(str(sizing['amount_per_trade']))
        self.max_open = sizing['max_open_positions']

        # Build signal evaluators with merged params
        self._entry_signals = []
        self._exit_signals = []
        self._filter_signals = []
        self._has_path_dependent = False

        for sig_cfg in self.config['signals']:
            spec = SIGNAL_REGISTRY[sig_cfg['signal_id']]
            merged_params = {**spec.default_params, **sig_cfg.get('param_overrides', {})}
            evaluation = sig_cfg.get('evaluation', {'mode': 'point_in_time'})
            entry = {
                'signal_id': sig_cfg['signal_id'],
                'spec': spec,
                'params': merged_params,
                'required': sig_cfg.get('required', False),
                'evaluation': evaluation,
            }
            if evaluation.get('mode') == 'path_dependent':
                self._has_path_dependent = True

            if sig_cfg['role'] == 'entry':
                self._entry_signals.append(entry)
            elif sig_cfg['role'] == 'exit':
                self._exit_signals.append(entry)
            elif sig_cfg['role'] == 'filter':
                self._filter_signals.append(entry)

        self.require_all_entry = self.config['entry_rules'].get('require_all', True)

        # Create entity state tracker if any signal uses path_dependent mode
        self._entity_tracker = EntityStateTracker() if self._has_path_dependent else None

    def run(self):
        """Execute the backtest. Returns dict with trades, metrics, entities_tested."""
        # Step 1: Pre-load all data via data service (or use pre-loaded panel)
        if self._preloaded_panel is not None:
            panel = self._preloaded_panel
        else:
            data_req = self.config['data_requirements']
            panel = get_panel_slice(
                self.asset_ids,
                data_req['layer_ids'],
                self.data_end,
                derived_ids=data_req.get('derived_ids'),
                derived_params=data_req.get('derived_params'),
            )

        if not panel:
            logger.warning("No data returned from get_panel_slice")
            return {
                'trades': [],
                'metrics': compute_metrics([]),
                'entities_tested': len(self.asset_ids),
            }

        # Step 2: Group by asset, sort by timestamp
        by_asset = defaultdict(list)
        for row in panel:
            by_asset[row['coin_id']].append(row)
        for rows in by_asset.values():
            rows.sort(key=lambda r: r['timestamp'])

        # Step 3: Collect all unique timestamps, sorted
        all_timestamps = sorted({row['timestamp'] for row in panel})

        # Filter to data window
        all_timestamps = [
            ts for ts in all_timestamps
            if self.data_start <= ts <= self.data_end
        ]

        # Step 4: Time-stepping
        tracker = PositionTracker(self.max_open)

        # Build lookup: (asset_id, timestamp) -> row
        row_lookup = {}
        for asset_id, rows in by_asset.items():
            for row in rows:
                row_lookup[(asset_id, row['timestamp'])] = row

        for ts in all_timestamps:
            # 4a: Check exits on open positions
            for asset_id in list(tracker._positions.keys()):
                row = row_lookup.get((asset_id, ts))
                if row is None:
                    continue
                pos = tracker.get_position(asset_id)
                price = row.get(self.price_field)
                if price is None:
                    continue

                exit_reason = self._check_exit(pos, ts, price, row)
                if exit_reason:
                    tracker.close(asset_id, ts, price, exit_reason)

            # 4b: Update filter signals for path-dependent tracking
            if self._entity_tracker and self._filter_signals:
                for asset_id in by_asset:
                    row = row_lookup.get((asset_id, ts))
                    if row is None:
                        continue
                    self._update_filters(asset_id, ts, row)

            # 4c: Check entries on unpositioned assets
            if tracker.can_open():
                for asset_id in by_asset:
                    if not tracker.can_open():
                        break
                    if tracker.has_position(asset_id):
                        continue
                    row = row_lookup.get((asset_id, ts))
                    if row is None:
                        continue
                    price = row.get(self.price_field)
                    if price is None or price <= 0:
                        continue

                    # Check filter signals first
                    if not self._check_filters(asset_id, ts, row):
                        continue

                    fired = self._check_entry(row, asset_id, ts)
                    if fired:
                        tracker.open(
                            asset_id, ts, price, self.amount_per_trade,
                            entry_reason=fired,
                        )

        # Step 5: Force-close remaining positions at last known prices
        if tracker._positions:
            last_prices = {}
            for asset_id in list(tracker._positions.keys()):
                for row in reversed(by_asset.get(asset_id, [])):
                    p = row.get(self.price_field)
                    if p is not None:
                        last_prices[asset_id] = p
                        break
            last_ts = all_timestamps[-1] if all_timestamps else self.data_end
            tracker.force_close_all(last_ts, last_prices)

        # Step 6: Compute metrics
        metrics = compute_metrics(tracker.closed_trades)

        return {
            'trades': tracker.closed_trades,
            'metrics': metrics,
            'entities_tested': len(self.asset_ids),
        }

    def _check_exit(self, pos, current_time, current_price, row):
        """Check mechanical exit conditions. Returns exit_reason string or None."""
        roi = pos.roi_pct(current_price)

        # Take profit
        if roi >= self.take_profit_pct:
            return 'take_profit'

        # Stop loss
        if roi <= self.stop_loss_pct:
            return 'stop_loss'

        # Timeout
        hold_delta = current_time - pos.entry_time
        hold_mins = hold_delta.total_seconds() / 60
        if hold_mins >= self.max_hold_minutes:
            return 'timeout'

        # Exit signals
        for sig in self._exit_signals:
            enriched = {**row, 'position_roi_pct': float(roi)}
            if sig['spec'].evaluate(enriched, **sig['params']):
                return 'exit_signal'

        return None

    def _update_filters(self, asset_id, current_time, row):
        """Update path-dependent filter signal state for an entity."""
        for sig in self._filter_signals:
            ev = sig['evaluation']
            if ev.get('mode') != 'path_dependent':
                continue

            fired = sig['spec'].evaluate(row, **sig['params'])

            # Apply lookback trimming before recording
            self._entity_tracker.apply_lookback(
                asset_id, sig['signal_id'],
                ev.get('lookback', 'all_history'),
                lookback_minutes=ev.get('lookback_minutes'),
                current_time=current_time,
            )

            self._entity_tracker.record_evaluation(
                asset_id, sig['signal_id'], current_time, fired,
            )

            # Check disqualification
            disq_mode = ev.get('disqualification', 'none')
            if disq_mode != 'none' and not fired:
                # Signal failed — check direction before applying
                direction = ev.get('direction', 'any')
                if direction == 'any' or self._entity_tracker.check_direction(
                    asset_id, sig['signal_id'], direction,
                ):
                    self._entity_tracker.apply_disqualification(
                        asset_id, disq_mode,
                        cooldown_minutes=ev.get('cooldown_minutes'),
                        current_time=current_time,
                    )

    def _check_filters(self, asset_id, current_time, row):
        """Check all filter signals. Returns True if entity passes all filters."""
        if not self._filter_signals:
            return True

        for sig in self._filter_signals:
            ev = sig['evaluation']

            if ev.get('mode') == 'path_dependent':
                # Use entity state tracker
                if not self._entity_tracker.is_available(asset_id, current_time):
                    if sig.get('required', False):
                        return False

                # Check confirmation if needed
                conf_mode = ev.get('confirmation', 'none')
                if conf_mode != 'none':
                    if not self._entity_tracker.check_confirmation(
                        asset_id, sig['signal_id'], conf_mode,
                        ev.get('confirmation_minutes', 0),
                        current_time,
                    ):
                        if sig.get('required', False):
                            return False
            else:
                # Point-in-time filter: just evaluate
                fired = sig['spec'].evaluate(row, **sig['params'])
                if not fired and sig.get('required', False):
                    return False

        return True

    def _check_entry(self, row, asset_id=None, current_time=None):
        """Check entry signals. Returns fired signal info dict or None."""
        results = {}
        for sig in self._entry_signals:
            ev = sig.get('evaluation', {'mode': 'point_in_time'})

            if ev.get('mode') == 'path_dependent' and self._entity_tracker:
                # Record and evaluate with path-dependent logic
                fired = sig['spec'].evaluate(row, **sig['params'])
                self._entity_tracker.record_evaluation(
                    asset_id, sig['signal_id'], current_time, fired,
                )

                # Check direction if specified
                direction = ev.get('direction', 'any')
                if fired and direction != 'any':
                    fired = self._entity_tracker.check_direction(
                        asset_id, sig['signal_id'], direction,
                    )

                # Check confirmation if specified
                conf_mode = ev.get('confirmation', 'none')
                if fired and conf_mode != 'none':
                    fired = self._entity_tracker.check_confirmation(
                        asset_id, sig['signal_id'], conf_mode,
                        ev.get('confirmation_minutes', 0),
                        current_time,
                    )

                results[sig['signal_id']] = fired
            else:
                fired = sig['spec'].evaluate(row, **sig['params'])
                results[sig['signal_id']] = fired

        if self.require_all_entry:
            if all(results.values()):
                return results
        else:
            if any(results.values()):
                return results

        return None
