from datetime import timedelta

from django.db import models
from django.db.models import F, ExpressionWrapper, DateTimeField


class UniverseQuerySet(models.QuerySet):
    def as_of(self, simulation_time):
        """Return assets that were universe members at simulation_time.

        Dispatches on the concrete model's UNIVERSE_TYPE:
        - event-driven: anchor_event <= T AND (membership_end IS NULL OR > T)
        - calendar-driven: all assets (no anchor filter), respecting membership_end
        """
        universe_type = self.model.UNIVERSE_TYPE
        if universe_type == 'event-driven':
            return self.filter(
                anchor_event__lte=simulation_time,
            ).exclude(
                membership_end__lte=simulation_time,
            )
        elif universe_type == 'calendar-driven':
            return self.exclude(
                membership_end__lte=simulation_time,
            )
        else:
            raise ValueError(
                f"Unknown UNIVERSE_TYPE on {self.model.__name__}: "
                f"{universe_type!r}"
            )


class FeatureLayerQuerySet(models.QuerySet):
    def as_of(self, simulation_time):
        """Return observations whose intervals had fully closed by simulation_time.

        End-of-interval: interval_end = timestamp + TEMPORAL_RESOLUTION <= simulation_time.
        Interval-start timestamp convention (WDP9).
        """
        resolution = self.model.TEMPORAL_RESOLUTION
        if resolution is None:
            raise ValueError(
                f"{self.model.__name__} has no TEMPORAL_RESOLUTION set"
            )
        interval_end = ExpressionWrapper(
            F('timestamp') + resolution,
            output_field=DateTimeField(),
        )
        return self.annotate(
            _interval_end=interval_end,
        ).filter(
            _interval_end__lte=simulation_time,
        )


class ReferenceTableQuerySet(models.QuerySet):
    def as_of(self, simulation_time):
        """Return events that occurred at or before simulation_time.

        Event-time: timestamp <= simulation_time.
        """
        return self.filter(timestamp__lte=simulation_time)
