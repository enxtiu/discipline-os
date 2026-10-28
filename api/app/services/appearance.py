"""The account's appearance: merge a write and check its ``base`` (stage 10).

``PATCH /settings`` may carry ``appearance`` (any subset of the keys; ``null`` = back to
"never synced") and ``base.appearance`` (what the edit started from). The rules are the
planner's (stage 9, ``services/planner/conflicts.py``), applied per key:

* ``base`` absent, or without ``appearance`` — last write wins;
* ``base.appearance: null`` — the edit expects an account that has no appearance yet;
* ``base.appearance: {…}`` — every key listed is compared with the stored one (a missing
  object counts as "unset", which never equals a value);
* a key that already *is* what this request sets does not conflict (a replay whose answer
  was lost, or the same choice made on two devices); keys not listed never conflict, so
  two devices changing different keys merge.

A conflict answers 409 ``edit_conflict`` with ``details.fields = ["appearance"]`` and
``details.current`` = the settings row as ``GET /settings`` shows it; nothing is written.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import Conflict
from app.models import UserSettings
from app.schemas.appearance import APPEARANCE_KEYS, AppearanceIn, merged
from app.schemas.settings import SettingsBaseIn, UserSettingsOut

__all__ = ["apply_appearance", "check_appearance_base"]


def _conflict(row: UserSettings) -> Conflict:
    current = UserSettingsOut.model_validate(row).model_dump(mode="json")
    return Conflict(
        "Changed on another device since this edit started",
        code="edit_conflict",
        details={"fields": ["appearance"], "current": current},
    )


def check_appearance_base(
    row: UserSettings, base: SettingsBaseIn | None, patch: AppearanceIn | None, *, sets: bool
) -> None:
    """Raise ``edit_conflict`` when ``base.appearance`` no longer matches the stored one.

    ``sets`` — the request writes ``appearance`` (``patch`` is then the new value, ``None`` = clear).
    """
    if base is None or "appearance" not in base.model_fields_set:
        return
    stored: dict[str, Any] | None = row.appearance
    if sets:
        target = None if patch is None else merged(stored, patch.changes())
        if target == stored:
            return  # already what this request sets
    else:
        target = stored
    expected = base.appearance
    if expected is None:
        if stored is not None:
            raise _conflict(row)
        return
    sent = patch.changes() if (sets and patch is not None) else {}
    for key in APPEARANCE_KEYS:
        if key not in expected.model_fields_set:
            continue
        now = None if stored is None else stored.get(key)
        if getattr(expected, key) == now:
            continue
        if key in sent and sent[key] == now:
            continue
        raise _conflict(row)


def apply_appearance(row: UserSettings, patch: AppearanceIn | None) -> None:
    """Write the merged object (a new dict, so the change is noticed), or clear it."""
    row.appearance = None if patch is None else merged(row.appearance, patch.changes())
