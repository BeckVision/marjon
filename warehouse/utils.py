"""Shared warehouse utilities."""

from django.db import models as dj_models

from warehouse.models import UniverseBase


def find_universe_fk(model):
    """Find the FK attname on a model that points to a UniverseBase subclass.

    Returns the Django field attname (e.g. 'coin_id', 'asset_id').

    Raises:
        ValueError: If no FK to UniverseBase is found.
    """
    for field in model._meta.get_fields():
        if isinstance(field, dj_models.ForeignKey) and issubclass(field.related_model, UniverseBase):
            return field.attname
    raise ValueError(f"No FK to UniverseBase found on {model.__name__}")
