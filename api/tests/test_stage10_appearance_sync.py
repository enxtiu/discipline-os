"""Stage 10: the account's appearance on the user's settings — «Одинаково на всех устройствах».

``GET /settings`` shows ``settings.appearance`` (``null`` = never synced), ``PATCH /settings``
accepts it (any subset of the keys, merged) with the stage-9 ``base`` rules, everything per
user, and the write is the one place a plan gate hooks into (``services/entitlements.py``).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from app.models import UserSettings
from app.schemas.appearance import DEFAULT_APPEARANCE
from tests.stage6_helpers import other_user

SETTINGS = "/api/v1/settings"
STICKERS = {
    "skin": "stickers",
    "mode": "light",
    "ground": "mint",
    "tilt": False,
    "shadow": "soft",
    "large_text": True,
    "reduce_motion": True,
}


async def read(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get(SETTINGS)
    assert response.status_code == 200, response.text
    return response.json()["settings"]


async def save(client: httpx.AsyncClient, **body: Any) -> dict[str, Any]:
    response = await client.patch(SETTINGS, json=body)
    assert response.status_code == 200, response.text
    return response.json()["settings"]


def conflict(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "edit_conflict"
    assert set(error["details"]) == {"fields", "current"}
    return error["details"]


# --------------------------------------------------------------------------- #
# Shape and round trip
# --------------------------------------------------------------------------- #
async def test_never_synced_is_null_in_settings_and_in_the_session(authed) -> None:
    assert (await read(authed))["appearance"] is None
    session = (await authed.get("/api/v1/auth/session")).json()
    assert session["settings"]["appearance"] is None


async def test_round_trip_a_whole_object(authed) -> None:
    saved = await save(authed, appearance=STICKERS)
    assert saved["appearance"] == STICKERS
    assert (await read(authed))["appearance"] == STICKERS
    # the session (what a device loads at start) shows the same object
    assert (await authed.get("/api/v1/auth/session")).json()["settings"]["appearance"] == STICKERS


async def test_a_partial_write_is_merged_and_the_first_one_starts_from_the_defaults(authed) -> None:
    first = await save(authed, appearance={"skin": "stickers"})
    assert first["appearance"] == {**DEFAULT_APPEARANCE, "skin": "stickers"}
    second = await save(authed, appearance={"ground": "peach", "tilt": False})
    assert second["appearance"] == {**DEFAULT_APPEARANCE, "skin": "stickers", "ground": "peach", "tilt": False}
    third = await save(authed, appearance={"mode": "system"})
    assert third["appearance"]["skin"] == "stickers" and third["appearance"]["mode"] == "system"


async def test_other_settings_are_untouched_by_an_appearance_write_and_the_other_way_round(authed) -> None:
    await save(authed, appearance={"skin": "stickers"}, week_start=0)
    settings = await save(authed, language="en")
    assert settings["appearance"]["skin"] == "stickers" and settings["week_start"] == 0
    assert settings["language"] == "en"


async def test_null_puts_the_account_back_to_never_synced(authed) -> None:
    await save(authed, appearance=STICKERS)
    assert (await save(authed, appearance=None))["appearance"] is None
    assert (await read(authed))["appearance"] is None


async def test_never_synced_is_sql_null_not_a_json_null(authed, db) -> None:
    await save(authed, appearance=STICKERS)
    await save(authed, appearance=None)
    assert (await db.execute(sa.text("SELECT appearance IS NULL FROM user_settings"))).scalar_one() is True


async def test_the_column_is_nullable_and_existing_rows_stay_null(db) -> None:
    info = (
        await db.execute(
            sa.text(
                "SELECT is_nullable, data_type, column_default FROM information_schema.columns "
                "WHERE table_name = 'user_settings' AND column_name = 'appearance'"
            )
        )
    ).one()
    assert info.is_nullable == "YES" and info.data_type == "jsonb" and info.column_default is None


# --------------------------------------------------------------------------- #
# Validation: known skins / modes / parameters only
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "appearance",
    [
        {"skin": "forge"},  # shown as «Скоро»: not a theme that can be chosen yet
        {"skin": "nonsense"},
        {"skin": None},
        {"mode": "auto"},
        {"ground": "red"},
        {"shadow": "medium"},
        {"tilt": "yes"},
        {"tilt": 1},
        {"large_text": "true"},
        {"reduce_motion": None},
        {"largeText": True},  # the wire names are snake_case
        {"font_size": 20},  # unknown key
        {"skin": "stickers", "extra": {}},
        "stickers",
        [],
    ],
)
async def test_unknown_skins_values_and_keys_are_refused_and_nothing_is_written(authed, appearance) -> None:
    await save(authed, appearance={"skin": "stickers"}, week_start=2)
    response = await authed.patch(SETTINGS, json={"appearance": appearance, "week_start": 5})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_error"
    after = await read(authed)
    assert after["appearance"] == {**DEFAULT_APPEARANCE, "skin": "stickers"} and after["week_start"] == 2


@pytest.mark.parametrize("base", [{"appearance": {"skin": "forge"}}, {"appearance": {"zoom": 1}}, {"theme": "x"}, "x"])
async def test_base_is_validated_as_well(authed, base) -> None:
    response = await authed.patch(SETTINGS, json={"appearance": {"skin": "stickers"}, "base": base})
    assert response.status_code == 422, response.text
    assert (await read(authed))["appearance"] is None


async def test_a_value_stored_by_another_build_never_breaks_get_settings(authed, db) -> None:
    await save(authed, appearance={"skin": "stickers", "ground": "mint"})
    await db.execute(
        sa.update(UserSettings).values(appearance={"skin": "future-skin", "ground": "mint", "tilt": "maybe", "x": 1})
    )
    await db.commit()
    shown = (await read(authed))["appearance"]
    assert shown == {
        **DEFAULT_APPEARANCE,
        "ground": "mint",
    }  # key by key: what validates stays, the rest is the default
    await db.execute(sa.update(UserSettings).values(appearance={"skin": "stickers"}))
    await db.commit()
    assert (await read(authed))["appearance"] == {**DEFAULT_APPEARANCE, "skin": "stickers"}


# --------------------------------------------------------------------------- #
# Per user
# --------------------------------------------------------------------------- #
async def test_two_users_are_isolated(authed) -> None:
    async with other_user() as other:
        await save(authed, appearance=STICKERS)
        assert (await read(other))["appearance"] is None  # B never sees A's
        mine = await save(other, appearance={"skin": "evolution", "mode": "light"})
        assert mine["appearance"] == {**DEFAULT_APPEARANCE, "mode": "light"}
        assert (await read(authed))["appearance"] == STICKERS  # and B's write did not touch A's
        # a base from one account is never compared with the other's row
        response = await other.patch(
            SETTINGS, json={"appearance": {"mode": "dark"}, "base": {"appearance": {"mode": "light"}}}
        )
        assert response.status_code == 200, response.text
        assert (await read(authed))["appearance"]["mode"] == "light"
        await save(other, appearance=None)
        assert (await read(authed))["appearance"] == STICKERS


async def test_a_client_cannot_name_another_users_row(authed) -> None:
    response = await authed.patch(SETTINGS, json={"appearance": {"skin": "stickers"}, "user_id": "x"})
    assert response.status_code == 422, response.text  # no user_id (or any unknown field) in a settings write


# --------------------------------------------------------------------------- #
# base → 409 edit_conflict (the stage-9 rules, per key)
# --------------------------------------------------------------------------- #
async def test_a_stale_base_is_a_409_with_the_current_row_and_writes_nothing(authed) -> None:
    await save(authed, appearance={"skin": "stickers", "ground": "lilac"})
    await save(authed, appearance={"ground": "mint"})  # the other device
    response = await authed.patch(
        SETTINGS,
        json={"appearance": {"ground": "peach"}, "language": "en", "base": {"appearance": {"ground": "lilac"}}},
    )
    details = conflict(response)
    assert details["fields"] == ["appearance"]
    assert details["current"] == await read(authed)  # the settings exactly as GET shows them
    assert details["current"]["appearance"]["ground"] == "mint"
    after = await read(authed)
    assert after["appearance"]["ground"] == "mint" and after["language"] == "ru"  # not even the other field


async def test_a_matching_base_writes(authed) -> None:
    await save(authed, appearance={"skin": "stickers", "ground": "lilac"})
    saved = await save(authed, appearance={"ground": "peach"}, base={"appearance": {"ground": "lilac"}})
    assert saved["appearance"]["ground"] == "peach" and saved["appearance"]["skin"] == "stickers"


async def test_devices_changing_different_keys_merge(authed) -> None:
    await save(authed, appearance={"skin": "stickers", "ground": "lilac", "mode": "dark"})
    # device 1 and device 2 both started from the same account; each depends on its own key only
    await save(authed, appearance={"ground": "mint"}, base={"appearance": {"ground": "lilac"}})
    saved = await save(authed, appearance={"mode": "light"}, base={"appearance": {"mode": "dark"}})
    assert saved["appearance"]["ground"] == "mint" and saved["appearance"]["mode"] == "light"


async def test_a_key_that_already_is_the_value_being_set_is_not_a_conflict(authed) -> None:
    await save(authed, appearance={"skin": "stickers", "ground": "lilac"})
    await save(authed, appearance={"ground": "mint"})  # the other device made the same choice …
    saved = await save(authed, appearance={"ground": "mint"}, base={"appearance": {"ground": "lilac"}})
    assert saved["appearance"]["ground"] == "mint"  # … and a replay whose answer was lost answers the same


async def test_a_replay_of_a_whole_write_is_not_a_conflict(authed) -> None:
    body = {"appearance": STICKERS, "base": {"appearance": None}}
    assert (await authed.patch(SETTINGS, json=body)).status_code == 200
    assert (await authed.patch(SETTINGS, json=body)).status_code == 200  # the account already is what it sets


async def test_base_null_expects_an_account_that_never_synced(authed) -> None:
    ok = await authed.patch(SETTINGS, json={"appearance": {"skin": "stickers"}, "base": {"appearance": None}})
    assert ok.status_code == 200, ok.text
    stale = await authed.patch(SETTINGS, json={"appearance": {"mode": "light"}, "base": {"appearance": None}})
    details = conflict(stale)
    assert details["current"]["appearance"]["skin"] == "stickers"
    assert (await read(authed))["appearance"]["mode"] == "dark"


async def test_a_base_with_values_never_matches_an_account_that_never_synced(authed) -> None:
    response = await authed.patch(
        SETTINGS, json={"appearance": {"skin": "stickers"}, "base": {"appearance": {"skin": "evolution"}}}
    )
    conflict(response)
    assert (await read(authed))["appearance"] is None


async def test_without_base_or_with_base_null_the_last_write_wins(authed) -> None:
    await save(authed, appearance={"ground": "mint"})
    assert (await save(authed, appearance={"ground": "peach"}))["appearance"]["ground"] == "peach"
    assert (await save(authed, appearance={"ground": "lemonade"}, base=None))["appearance"]["ground"] == "lemonade"
    assert (await save(authed, appearance={"ground": "lilac"}, base={}))["appearance"]["ground"] == "lilac"


async def test_base_alone_is_a_precondition_for_the_other_fields(authed) -> None:
    await save(authed, appearance={"skin": "stickers"})
    response = await authed.patch(SETTINGS, json={"language": "en", "base": {"appearance": {"skin": "evolution"}}})
    conflict(response)
    assert (await read(authed))["language"] == "ru"
    ok = await authed.patch(SETTINGS, json={"language": "en", "base": {"appearance": {"skin": "stickers"}}})
    assert ok.status_code == 200, ok.text


async def test_clearing_honours_base(authed) -> None:
    await save(authed, appearance={"skin": "stickers"})
    stale = await authed.patch(SETTINGS, json={"appearance": None, "base": {"appearance": {"skin": "evolution"}}})
    conflict(stale)
    assert (await read(authed))["appearance"] is not None
    ok = await authed.patch(SETTINGS, json={"appearance": None, "base": {"appearance": {"skin": "stickers"}}})
    assert ok.status_code == 200 and ok.json()["settings"]["appearance"] is None


# --------------------------------------------------------------------------- #
# The plan gate (the seam for subscriptions)
# --------------------------------------------------------------------------- #
async def test_a_plan_without_the_feature_cannot_write_but_still_reads(authed, monkeypatch) -> None:
    await save(authed, appearance={"skin": "stickers"})
    week_start = (await read(authed))["week_start"]
    from app.services import entitlements

    asked: list[tuple[Any, str]] = []

    def deny(user: Any, feature: str) -> bool:
        asked.append((user.id, feature))
        return False

    monkeypatch.setattr(entitlements, "plan_allows", deny)
    response = await authed.patch(SETTINGS, json={"appearance": {"skin": "evolution"}, "week_start": 3})
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "feature_not_in_plan" and error["details"] == {"feature": "appearance_sync"}
    assert asked and asked[0][1] == entitlements.FEATURE_APPEARANCE_SYNC
    after = await read(authed)  # reads are never gated, and nothing was written
    assert after["appearance"]["skin"] == "stickers" and after["week_start"] == week_start
    # the other settings do not ask the plan
    asked.clear()
    assert (await authed.patch(SETTINGS, json={"week_start": 3})).status_code == 200
    assert asked == []


# --------------------------------------------------------------------------- #
# Backup: the column travels, and a restore re-checks it
# --------------------------------------------------------------------------- #
async def test_a_backup_carries_the_appearance_and_a_restore_refuses_a_bad_one(authed, tmp_path) -> None:
    from app.services.restore import RestoreError
    from tests.test_stage8_export import restore_file, rewrite_backup, save_backup

    await save(authed, appearance=STICKERS)
    path = await save_backup(authed, tmp_path / "a.zip")

    def carried(document: dict[str, Any], manifest: dict[str, Any]) -> None:
        assert document["tables"]["settings"][0]["appearance"] == STICKERS

    rewrite_backup(path, tmp_path / "same.zip", carried, sign=True)

    def bad(document: dict[str, Any], manifest: dict[str, Any]) -> None:
        document["tables"]["settings"][0]["appearance"] = {**STICKERS, "skin": "forge"}

    with pytest.raises(RestoreError, match=r"settings\.appearance: not an appearance object"):
        await restore_file(rewrite_backup(path, tmp_path / "bad.zip", bad, sign=True))
