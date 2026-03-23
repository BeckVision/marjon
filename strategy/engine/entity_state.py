"""Entity state tracking for path-dependent signal evaluation.

In-memory only — created per backtest run. No models, no DB.
"""

from collections import defaultdict


class EntityState:
    """Per-entity path-dependent state."""

    def __init__(self, asset_id):
        self.asset_id = asset_id
        self.disqualified = False
        self.cooldown_until = None
        # signal_id -> list of (timestamp, value, fired)
        self.value_history = defaultdict(list)
        # signal_id -> accumulated minutes of confirmation
        self.confirmation_accum = defaultdict(float)

    def is_available(self, current_time):
        """True if entity is eligible for entry at current_time."""
        if self.disqualified:
            return False
        if self.cooldown_until is not None and current_time < self.cooldown_until:
            return False
        return True


class EntityStateTracker:
    """Manages EntityState for all entities in a run."""

    def __init__(self):
        self._states = {}

    def get_or_create(self, asset_id):
        """Get or create EntityState for an asset."""
        if asset_id not in self._states:
            self._states[asset_id] = EntityState(asset_id)
        return self._states[asset_id]

    def is_available(self, asset_id, current_time):
        """Check if entity is available for entry."""
        state = self._states.get(asset_id)
        if state is None:
            return True  # no state yet = available
        return state.is_available(current_time)

    def record_evaluation(self, asset_id, signal_id, timestamp, fired, value=None):
        """Record a signal evaluation for path-dependent tracking."""
        state = self.get_or_create(asset_id)
        state.value_history[signal_id].append((timestamp, value, fired))

    def apply_disqualification(self, asset_id, mode, cooldown_minutes=None,
                               current_time=None):
        """Apply disqualification to an entity.

        Args:
            asset_id: Entity to disqualify.
            mode: 'permanent', 'cooldown', or 'none'.
            cooldown_minutes: Minutes to block (only for 'cooldown' mode).
            current_time: Current timestamp (required for 'cooldown').
        """
        state = self.get_or_create(asset_id)

        if mode == 'permanent':
            state.disqualified = True
        elif mode == 'cooldown':
            if cooldown_minutes is not None and current_time is not None:
                from datetime import timedelta
                state.cooldown_until = current_time + timedelta(
                    minutes=cooldown_minutes,
                )
        # mode == 'none': do nothing

    def check_direction(self, asset_id, signal_id, direction):
        """Check how the entity entered the qualified zone.

        Args:
            direction: 'fell_into', 'started_in', 'rose_into', 'any'.

        Returns:
            True if the direction condition is met.
        """
        if direction == 'any':
            return True

        state = self._states.get(asset_id)
        if state is None:
            return direction == 'started_in'

        history = state.value_history.get(signal_id, [])
        if len(history) < 2:
            # Only one data point — treat as "started_in"
            return direction == 'started_in'

        current_fired = history[-1][2]
        prev_fired = history[-2][2]

        if direction == 'started_in':
            # Signal has been true from the start (never been false)
            return all(h[2] for h in history)

        if direction == 'fell_into':
            # Was true, became false — "fell into" disqualification zone
            return not current_fired and prev_fired

        if direction == 'rose_into':
            # Was false, became true — "rose into" qualified zone
            return current_fired and not prev_fired

        return False

    def check_confirmation(self, asset_id, signal_id, mode, minutes,
                           current_time, timestep_minutes=5):
        """Check confirmation requirement.

        Args:
            mode: 'continuous', 'cumulative', or 'none'.
            minutes: Required confirmation duration.
            timestep_minutes: Duration of each timestep.

        Returns:
            True if confirmation is met.
        """
        if mode == 'none':
            return True

        state = self.get_or_create(asset_id)
        history = state.value_history.get(signal_id, [])

        if not history:
            return False

        if mode == 'continuous':
            # Signal must have been true for the last N minutes continuously
            required_steps = max(1, int(minutes / timestep_minutes))
            if len(history) < required_steps:
                return False
            recent = history[-required_steps:]
            return all(h[2] for h in recent)

        if mode == 'cumulative':
            # Count total minutes where signal was true
            state.confirmation_accum.setdefault(signal_id, 0)
            if history[-1][2]:
                state.confirmation_accum[signal_id] += timestep_minutes
            else:
                # Reset cumulative on false (depends on use case)
                pass
            return state.confirmation_accum[signal_id] >= minutes

        return False

    def apply_lookback(self, asset_id, signal_id, lookback_mode,
                       lookback_minutes=None, graduation_time=None,
                       current_time=None):
        """Trim value history based on lookback setting.

        Should be called before direction/confirmation checks.

        Args:
            lookback_mode: 'all_history', 'rolling_window', 'since_graduation'.
        """
        if lookback_mode == 'all_history':
            return  # no trimming

        state = self._states.get(asset_id)
        if state is None:
            return

        history = state.value_history.get(signal_id)
        if not history:
            return

        if lookback_mode == 'rolling_window' and lookback_minutes and current_time:
            from datetime import timedelta
            cutoff = current_time - timedelta(minutes=lookback_minutes)
            state.value_history[signal_id] = [
                h for h in history if h[0] >= cutoff
            ]

        elif lookback_mode == 'since_graduation' and graduation_time:
            state.value_history[signal_id] = [
                h for h in history if h[0] >= graduation_time
            ]
