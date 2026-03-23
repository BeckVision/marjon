"""Signal registry — SignalSpec + SIGNAL_REGISTRY.

Mirrors DerivedFeatureSpec / DERIVED_REGISTRY pattern exactly.
Signals are pure functions: (row, **params) -> bool.
"""

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class SignalSpec:
    signal_id: str              # "SG-001"
    name: str
    category: str               # "holder", "price", "volume"
    source_layers: list         # ["FL-001"]
    evaluate: Callable          # (row, **params) -> bool — pure function
    default_params: dict
    derived_features: list      # ["DF-002"] — derived features signal needs
    description: str = ""
    param_ranges: dict = field(default_factory=dict)


SIGNAL_REGISTRY = {}


def register_signal(spec):
    """Register a SignalSpec in the global registry."""
    SIGNAL_REGISTRY[spec.signal_id] = spec
    return spec
