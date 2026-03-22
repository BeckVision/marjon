"""Pipeline-specific exceptions."""


class BudgetExhausted(Exception):
    """Raised when a CU/API budget is exhausted for the current period.

    Signals the orchestrator to stop the current step cleanly
    rather than treating it as a per-coin error.
    """
    pass
