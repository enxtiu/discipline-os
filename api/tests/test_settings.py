"""Settings API: profile, auto-recompute, manual targets, safety floor."""

from __future__ import annotations


async def _settings(client) -> dict:
    response = await client.get("/api/v1/settings")
    assert response.status_code == 200, response.text
    return response.json()


async def test_settings_bootstrap_is_idempotent(authed) -> None:
    first = await _settings(authed)
    second = await _settings(authed)
    assert first["settings"]["user_id"] == second["settings"]["user_id"]
    assert first["settings"]["language"] == "ru"
    assert first["settings"]["targets_manual"] is False
    assert first["targets"]["target_kcal"] is None  # no body data yet
    assert "missing_body_params" in first["targets"]["warnings"]


async def test_onboarding_computes_and_stores_targets(authed) -> None:
    response = await authed.post(
        "/api/v1/settings/onboarding",
        json={
            "language": "ru",
            "timezone": "Europe/Moscow",
            "sex": "male",
            "birth_date": "1990-05-05",
            "height_cm": 180,
            "weight_kg": 80,
            "activity_level": "moderate",
            "goal_mode": "lose",
            "goal_rate_pct": 15,
            "target_steps": 10000,
            "notifications": {"water": False},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    settings, targets = body["settings"], body["targets"]

    assert settings["timezone"] == "Europe/Moscow"
    assert settings["onboarding_completed"] is True
    assert settings["weight_kg"] == 80.0
    assert settings["notification_prefs"]["water"] is False
    assert settings["notification_prefs"]["task_reminders"] is True  # merged default

    # Mifflin: 10*80 + 6.25*180 - 5*age + 5, age from birth date
    assert targets["formula_used"] == "mifflin_st_jeor"
    assert targets["bmr_kcal"] is not None and targets["tdee_kcal"] > targets["bmr_kcal"]
    assert targets["target_kcal"] < targets["tdee_kcal"]  # deficit
    assert targets["target_protein_g"] == 160
    assert targets["target_fiber_g"] == round(14 * targets["target_kcal"] / 1000)
    assert targets["target_steps"] == 10000
    assert targets["safe_floor_kcal"] == 1500

    # weight became a body-metric row for today
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import BodyMetric

    async with SessionLocal() as session:
        rows = (await session.execute(select(BodyMetric))).scalars().all()
    assert len(rows) == 1 and float(rows[0].weight_kg) == 80.0


async def test_patch_body_params_recomputes_targets(authed) -> None:
    await authed.post(
        "/api/v1/settings/onboarding",
        json={
            "sex": "male",
            "birth_date": "1990-05-05",
            "height_cm": 180,
            "weight_kg": 80,
            "activity_level": "moderate",
        },
    )
    before = (await _settings(authed))["targets"]["target_kcal"]

    response = await authed.patch(
        "/api/v1/settings", json={"activity_level": "active", "goal_mode": "gain", "goal_rate_pct": 10}
    )
    assert response.status_code == 200
    after = response.json()["targets"]
    assert after["target_kcal"] > before
    assert after["deficit_pct"] < 0  # surplus -> negative deficit


async def test_weight_patch_updates_history_without_duplicating(authed) -> None:
    await authed.patch("/api/v1/settings", json={"weight_kg": 80})
    await authed.patch("/api/v1/settings", json={"weight_kg": 79.4})

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import BodyMetric

    async with SessionLocal() as session:
        rows = (await session.execute(select(BodyMetric))).scalars().all()
    assert len(rows) == 1
    assert float(rows[0].weight_kg) == 79.4


async def test_manual_targets_win_until_recompute(authed) -> None:
    await authed.post(
        "/api/v1/settings/onboarding",
        json={"sex": "male", "birth_date": "1990-05-05", "height_cm": 180, "weight_kg": 80},
    )
    manual = await authed.put(
        "/api/v1/settings/targets",
        json={"target_kcal": 2600, "target_protein_g": 170, "target_fat_g": 70},
    )
    assert manual.status_code == 200, manual.text
    body = manual.json()
    assert body["is_manual"] is True
    assert body["target_kcal"] == 2600

    # changing body params must NOT overwrite manual targets
    await authed.patch("/api/v1/settings", json={"weight_kg": 95})
    assert (await _settings(authed))["targets"]["target_kcal"] == 2600

    # ... until the user asks for auto again
    recomputed = await authed.post("/api/v1/settings/targets/recompute")
    assert recomputed.status_code == 200
    assert recomputed.json()["is_manual"] is False
    assert recomputed.json()["target_kcal"] != 2600


async def test_manual_target_below_the_safe_floor_needs_confirmation(authed) -> None:
    await authed.post(
        "/api/v1/settings/onboarding",
        json={"sex": "female", "birth_date": "1995-01-01", "height_cm": 165, "weight_kg": 60},
    )
    refused = await authed.put("/api/v1/settings/targets", json={"target_kcal": 1100})
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "target_below_safe_floor"
    assert refused.json()["error"]["details"]["safe_floor_kcal"] == 1200

    accepted = await authed.put("/api/v1/settings/targets", json={"target_kcal": 1100, "confirm_floor": True})
    assert accepted.status_code == 200
    assert accepted.json()["target_kcal"] == 1100
    assert "below_safe_floor_confirmed" in accepted.json()["warnings"]

    # the absolute floor still holds
    hard_no = await authed.put("/api/v1/settings/targets", json={"target_kcal": 900, "confirm_floor": True})
    assert hard_no.status_code in (422,)


async def test_macros_that_blow_the_budget_are_rejected(authed) -> None:
    await authed.post(
        "/api/v1/settings/onboarding",
        json={"weight_kg": 80, "height_cm": 180, "sex": "male", "birth_date": "1990-01-01"},
    )
    response = await authed.put(
        "/api/v1/settings/targets",
        json={"target_kcal": 2000, "target_protein_g": 300, "target_fat_g": 120},
    )
    assert response.status_code == 422
    assert (
        response.json()["error"]["code"] == "macros_exceed_calories"
        or response.json()["error"]["code"] == "macros_exceed_kcal"
    )


async def test_target_preview_is_stateless(authed) -> None:
    response = await authed.post(
        "/api/v1/settings/targets/preview",
        json={
            "sex": "female",
            "age_years": 30,
            "height_cm": 165,
            "weight_kg": 60,
            "activity_level": "light",
            "goal_mode": "lose",
            "goal_rate_pct": 10,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["target_kcal"] is not None
    assert body["safe_floor_kcal"] == 1200
    # nothing was stored
    stored = await _settings(authed)
    assert stored["settings"]["weight_kg"] is None


async def test_body_fat_switches_to_katch_mcardle(authed) -> None:
    response = await authed.post(
        "/api/v1/settings/targets/preview",
        json={"sex": "male", "age_years": 30, "height_cm": 180, "weight_kg": 80, "body_fat_pct": 15},
    )
    assert response.json()["formula_used"] == "katch_mcardle"


async def test_quiet_hours_and_language_round_trip(authed) -> None:
    response = await authed.patch(
        "/api/v1/settings",
        json={
            "language": "en",
            "quiet_hours_start": "22:30",
            "quiet_hours_end": "06:30",
            "quiet_hours_enabled": True,
            "timezone": "America/New_York",
        },
    )
    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["language"] == "en"
    assert settings["quiet_hours_start"] == "22:30:00"
    assert settings["timezone"] == "America/New_York"


async def test_unknown_timezone_is_rejected(authed) -> None:
    response = await authed.patch("/api/v1/settings", json={"timezone": "Mars/Olympus_Mons"})
    assert response.status_code == 422


async def test_lowering_the_safe_floor_needs_confirmation(authed) -> None:
    await authed.post(
        "/api/v1/settings/onboarding",
        json={"sex": "male", "birth_date": "1990-01-01", "height_cm": 180, "weight_kg": 80},
    )
    refused = await authed.patch("/api/v1/settings", json={"safe_floor_kcal": 1200})
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "target_below_safe_floor"

    ok = await authed.patch("/api/v1/settings", json={"safe_floor_kcal": 1200, "confirm_floor": True})
    assert ok.status_code == 200
    assert ok.json()["settings"]["safe_floor_kcal"] == 1200


async def test_i18n_catalogue_has_both_languages(authed) -> None:
    response = await authed.get("/api/v1/meta/i18n/messages")
    assert response.status_code == 200
    labels = response.json()
    codes = {item["code"] for item in labels}
    assert "meal.breakfast" in codes
    assert all(item["ru"] and item["en"] for item in labels)
