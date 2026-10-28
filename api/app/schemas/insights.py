"""Stage 10 — the one calm insight of «Сегодня» (``GET /insights/today``).

At most one observation, computed by the server from the user's own history in their
timezone: a day's protein, fibre, water or steps above its 4-week average. Never about
calories, fat or carbs, never "less than", and never for a day whose food stayed below
the safe floor — see ``app.services.insights``. The client renders the sentence for
``metric`` and ``day``; it never computes these numbers.
"""

from __future__ import annotations

from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, Field

InsightMetricLiteral = Literal["protein", "fiber", "water", "steps"]
InsightDayLiteral = Literal["today", "yesterday"]


class InsightOut(BaseModel):
    metric: InsightMetricLiteral = Field(description="what the observation is about (more of it than usual)")
    day: InsightDayLiteral = Field(description="today (so far) or yesterday (a finished day)")
    date: Date = Field(description="the local day the observation is about")
    value: float = Field(description="that day's amount: protein / fibre in g, water in ml, steps")
    average: float = Field(description="the mean of the 28 days before it, over the days that have data")
    unit: Literal["g", "ml", "steps"]
    window_days: int = Field(description="how far back the average looks (28 days)")
    days_with_data: int = Field(description="the days of the window the average is taken over (at least 7)")


class TodayInsightOut(BaseModel):
    date: Date = Field(description="your today")
    insight: InsightOut | None = Field(description="null = nothing worth saying today (the screen shows nothing)")
