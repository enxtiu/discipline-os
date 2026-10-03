"""Versioned prompts, stored as files in ``app/ai/prompts/<id>.<version>.md``.

A prompt file has a tiny header (``id``, ``version``) and two sections,
``# system`` and ``# user``. Changing a prompt means adding a new version file
and bumping ``CURRENT`` — every ``AiJob`` row records ``prompt_id`` and
``prompt_version``, so results stay attributable to the exact text that
produced them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"

# prompt id → version in use
CURRENT: dict[str, str] = {
    "analyze_meal_photo": "v1",
    "parse_food_text": "v1",
}

_SECTION = re.compile(r"^# (system|user)\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Prompt:
    id: str
    version: str
    system: str
    user_template: str

    def render_user(self, **values: str) -> str:
        """Plain placeholder substitution (no str.format: user text may contain braces).

        ``text`` is substituted last so a user typing "{locale}" cannot
        reach the other placeholders.
        """
        rendered = self.user_template
        ordered = sorted(values.items(), key=lambda item: item[0] == "text")
        for key, value in ordered:
            rendered = rendered.replace("{" + key + "}", value)
        return rendered


@lru_cache(maxsize=16)
def load_prompt(prompt_id: str, version: str | None = None) -> Prompt:
    version = version or CURRENT[prompt_id]
    path = PROMPTS_DIR / f"{prompt_id}.{version}.md"
    raw = path.read_text(encoding="utf-8")
    header, _, body = raw.partition("\n---\n")
    meta = dict(line.split(":", 1) for line in header.strip().strip("-").strip().splitlines() if ":" in line)
    meta = {key.strip(): value.strip() for key, value in meta.items()}
    if meta.get("id") != prompt_id or meta.get("version") != version:
        raise ValueError(f"{path.name}: header id/version does not match the file name")
    parts = _SECTION.split(body)
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if not sections.get("system") or not sections.get("user"):
        raise ValueError(f"{path.name}: needs '# system' and '# user' sections")
    return Prompt(id=prompt_id, version=version, system=sections["system"], user_template=sections["user"])
