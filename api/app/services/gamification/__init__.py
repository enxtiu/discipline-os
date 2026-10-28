"""Stage 8 — gamification: daily score, XP, levels, streaks, achievements, quests.

* ``context``      — one run's clock, day, targets, floor, zone, weights
* ``score``        — the 0–100 daily score and the rings (calorie curve peaks *in* the zone)
* ``xp``           — the deterministic XP ledger (open window, frozen days, permanent awards)
* ``levels``       — the 30-level curve
* ``streaks``      — daily streaks and the weekly plan-aware workout streak; freeze tokens
                     only when ``STREAK_FREEZES_ENABLED`` (off by default: a miss breaks)
* ``achievements`` — metrics, unlocks, progress
* ``quests``       — daily/weekly selection, progress, claim, expiry
* ``engine``       — the per-user run shared by the jobs and the endpoints

Per-day aggregates come from ``app.services.day_metrics`` (shared with stats/export).
Binding safety rules: nothing rewards intake below the safe floor or not logging;
burned calories never become food budget; XP is never negative; rest days never
break a streak.
"""
