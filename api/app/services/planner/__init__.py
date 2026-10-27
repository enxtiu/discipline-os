"""Planner (stage 6): PARA containers, tasks, views, quick add, time tracking, journal.

Modules:

* ``common``      — the request clock (user timezone + now), local ↔ instant
  conversion, owned-row lookups, tag normalisation, advisory locks;
* ``recur``       — RRULE validation/canonicalisation, presets, bounded expansion;
* ``tasks``       — task CRUD, status rules, subtasks, recurrence, reminders, tags;
* ``loader``      — batch loading of everything a task list shows (no N+1);
* ``views``       — today / inbox / upcoming / calendar / tag / archive / counts / search;
* ``containers``  — areas, projects, resources, tags;
* ``time_tracking`` — timer start/stop/switch, manual entries, summaries;
* ``journal``     — the daily journal and the cross-domain day summary;
* ``quick_add``   — natural-language quick add through ``services.task_parser``.

Rules every module keeps: every query is scoped by the caller's ``user_id``;
creates are idempotent by client UUID; the client never converts between wall
clock and instants — the server does, with ``settings.timezone``.
"""
