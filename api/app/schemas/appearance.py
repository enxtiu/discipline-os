"""The account's appearance (stage 10): what «Одинаково на всех устройствах» keeps in sync.

One JSON object on the user's settings (``user_settings.appearance``), ``NULL`` = never
synced. It mirrors ``web/src/lib/appearance.ts`` (the per-device store); the names are
snake_case on the wire as everywhere in this API.

* ``skin`` — the theme that can be chosen today (the ones shown as «Скоро» are not accepted);
* ``mode`` — ``light`` | ``dark`` | ``system`` (``system`` follows each device's OS);
* ``ground`` · ``tilt`` · ``shadow`` — the stickers theme's own parameters;
* ``large_text`` · ``reduce_motion`` — the two switches that apply to every theme.

Unknown keys and unknown values answer 422. The stored object is always complete (a
partial write is merged onto the stored one, or onto the defaults the first time).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import StrictBool, model_validator

from app.schemas.common import ApiModel

SkinLiteral = Literal["evolution", "stickers"]
ModeLiteral = Literal["light", "dark", "system"]
GroundLiteral = Literal["lilac", "mint", "peach", "lemonade"]
ShadowLiteral = Literal["hard", "soft"]

#: Same as ``DEFAULT_APPEARANCE`` in ``web/src/lib/appearance.ts``.
DEFAULT_APPEARANCE: dict[str, Any] = {
    "skin": "evolution",
    "mode": "dark",
    "ground": "lilac",
    "tilt": True,
    "shadow": "hard",
    "large_text": False,
    "reduce_motion": False,
}
APPEARANCE_KEYS: tuple[str, ...] = tuple(DEFAULT_APPEARANCE)


class AppearanceOut(ApiModel):
    """The whole object, as stored and as ``GET /settings`` shows it."""

    skin: SkinLiteral
    mode: ModeLiteral
    ground: GroundLiteral
    tilt: StrictBool
    shadow: ShadowLiteral
    large_text: StrictBool
    reduce_motion: StrictBool


class AppearanceIn(ApiModel):
    """A write: any subset of the keys (merged onto the stored object). ``null`` is not a value."""

    skin: SkinLiteral | None = None
    mode: ModeLiteral | None = None
    ground: GroundLiteral | None = None
    tilt: StrictBool | None = None
    shadow: ShadowLiteral | None = None
    large_text: StrictBool | None = None
    reduce_motion: StrictBool | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> AppearanceIn:
        empty = sorted(name for name in self.model_fields_set if getattr(self, name) is None)
        if empty:
            raise ValueError(f"appearance.{empty[0]} cannot be null (send the key only when it changes)")
        return self

    def changes(self) -> dict[str, Any]:
        """Only the keys that were sent."""
        return {name: getattr(self, name) for name in APPEARANCE_KEYS if name in self.model_fields_set}


class AppearanceBaseIn(AppearanceIn):
    """``base.appearance``: the values the edit started from, for the keys it depends on."""


def merged(current: dict[str, Any] | None, changes: dict[str, Any]) -> dict[str, Any]:
    """The complete object after a write."""
    return {**DEFAULT_APPEARANCE, **(current or {}), **changes}


def coerce_stored(value: Any) -> dict[str, Any] | None:
    """What a row holds, as a complete valid object (or ``None``): a value that does not
    validate (written by another build) falls back key by key to the defaults, so
    ``GET /settings`` never fails on it."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return dict(DEFAULT_APPEARANCE)
    out = dict(DEFAULT_APPEARANCE)
    for name in APPEARANCE_KEYS:
        if name not in value:
            continue
        try:
            out[name] = getattr(AppearanceOut.model_validate({**out, name: value[name]}), name)
        except ValueError:
            continue
    return out
