"""Food database importers (stage 2).

Sources ship as small iterator modules; ``base.run_import`` is the single
upsert pipeline. Add a source by writing ``iterate_<name>(path) -> RawFood``.
"""

from app.importers.base import ImportStats, RawFood, run_import

__all__ = ["ImportStats", "RawFood", "run_import"]
