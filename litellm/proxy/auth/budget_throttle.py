"""
Throttle a key after it exceeds its own ``max_budget`` instead of blocking it.

When a key opts in via ``throttle_on_budget_exceeded`` and a global
``budget_exceeded_throttle_percentage`` is configured, an over-budget key keeps
serving requests but at a reduced TPM/RPM (the configured percentage of its
configured limits). The decision (over budget + opted in) is made once during
auth; the scaling is recomputed from the key's original limits on every request
so it never compounds across requests.
"""

import math
from typing import Optional

import litellm
from litellm.proxy._types import UserAPIKeyAuth


def budget_throttle_percentage() -> Optional[float]:
    """
    The global throttle percentage, or None when throttling is disabled /
    misconfigured (in which case an over-budget key is hard-blocked, the safe
    default).
    """
    pct = litellm.budget_exceeded_throttle_percentage
    if not isinstance(pct, (int, float)) or isinstance(pct, bool):
        return None
    if not 0 < pct <= 1:
        return None
    return float(pct)


def should_throttle_budget_exceeded(valid_token: UserAPIKeyAuth) -> bool:
    """
    True when a key that exceeded its own ``max_budget`` should be throttled
    rather than blocked: it opted in and a valid global percentage is set.
    """
    if (valid_token.metadata or {}).get("throttle_on_budget_exceeded") is not True:
        return False
    return budget_throttle_percentage() is not None


def throttled_limit(limit: Optional[int], pct: Optional[float]) -> Optional[int]:
    """
    Scale a TPM/RPM limit to ``pct`` of its value, keeping a trickle of at least
    1 so a throttled key is slowed rather than fully locked out. An unset limit
    or unset percentage leaves the limit unchanged.
    """
    if limit is None or pct is None:
        return limit
    return max(1, math.floor(limit * pct))
