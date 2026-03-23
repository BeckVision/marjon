"""Strategy config loader — mirrors load_universe_config() pattern."""

import importlib


def load_strategy_config(name):
    """Load strategy/strategies/{name}.py and return STRATEGY dict."""
    module_path = f"strategy.strategies.{name}"
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        raise ValueError(
            f"No strategy config found at {module_path}. "
            f"Create strategy/strategies/{name}.py with a STRATEGY dict."
        )
    if not hasattr(module, 'STRATEGY'):
        raise ValueError(
            f"{module_path} does not define a STRATEGY dict."
        )
    return module.STRATEGY
