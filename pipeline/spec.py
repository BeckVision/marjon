"""PipelineSpec: declarative configuration for a per-coin pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable


@dataclass
class PipelineSpec:
    layer_id: str                    # "FL-001", "FL-002", "RD-001"
    model: type                      # OHLCVCandle, HolderSnapshot, RawTransaction
    overlap: timedelta               # timedelta(minutes=30) or timedelta(minutes=5)
    fetch: Callable                  # (mint, pool, start, end, **kw) -> (raw, meta)
    conform: Callable                # (raw, mint, pool, **kw) -> canonical or (canonical, skipped)
    load: Callable                   # (mint, start, end, canonical, skipped) -> None
    requires_pool: bool = True       # FL-002 does not need pool
    conform_returns_tuple: bool = False  # RD-001 conform returns (parsed, skipped)
    pre_flight: Callable | None = None   # (coin, pool, start, end, **kw) -> None
    reconcile: Callable | None = None    # (canonical, skipped, start, end, meta, mint, **kw) -> dict
    compute_completeness: Callable | None = None  # override default
