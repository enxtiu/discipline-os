"""Stage 9 — the optional one-way Notion mirror.

* :mod:`.client` — the HTTP client (pinned ``Notion-Version``, pace, retries, budget,
  typed errors);
* :mod:`.schema` — the databases DisciplineOS creates and their properties (ru/en);
* :mod:`.mapping` — local rows → Notion property values (reusing the existing services);
* :mod:`.sync` — connect, create databases, the idempotent resumable sync run, the job.
"""
