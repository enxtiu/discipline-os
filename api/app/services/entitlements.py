"""Plan gates (stage 10): the one place that will answer «does this account's plan include X?».

Today every account has every feature, so :func:`plan_allows` is ``True``. The app is meant
to become a multi-user service with subscriptions; a feature that should depend on the plan
asks here, with the *user* (never a global setting), and the endpoint refuses with
403 ``feature_not_in_plan``. Reads are never gated — a client must always be able to show
what the account holds. To add a plan later: store it per user (a column or a subscription
table keyed by ``user_id``), look it up in :func:`plan_allows`, nothing else changes.
"""

from __future__ import annotations

from app.core.errors import Forbidden
from app.models import User

#: Features a plan can include. Keep the ids stable: clients and billing refer to them.
FEATURE_APPEARANCE_SYNC = "appearance_sync"


def plan_allows(user: User, feature: str) -> bool:
    """Does ``user``'s plan include ``feature``? (Everything, for now.)"""
    return True


def require_feature(user: User, feature: str) -> None:
    """Raise 403 ``feature_not_in_plan`` unless the user's plan includes ``feature``."""
    if not plan_allows(user, feature):
        raise Forbidden(
            "This feature is not included in your plan.",
            code="feature_not_in_plan",
            details={"feature": feature},
        )
