"""Stage-8 review fixes on the restore side (review 8: M2, M3, L1, L2).

L1 — a backup archive is checked before anything in it is inflated: the declared
sizes (``manifest.json`` ≤ 1 MiB, ``data.json`` ≤ ``BACKUP_MAX_DATA_BYTES``), the
compression ratio, and reading stops at the limit even when a header lies — a tiny
archive never turns into hundreds of MiB of memory.
"""

from __future__ import annotations

import io
import struct
import tracemalloc
import zipfile
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services.restore import RestoreError, read_backup
from tests.stage8_helpers import _fresh_seed_cache  # noqa: F401  (autouse fixture)

pytestmark = pytest.mark.anyio
MiB = 1 << 20


# --------------------------------------------------------------------------- #
# L1 — sizes and ratios before anything is inflated
# --------------------------------------------------------------------------- #
def _blank_json(size: int) -> bytes:
    """Valid JSON: ``{}`` padded with ``size`` blanks (deflates about 1 000×)."""
    return b"{" + b" " * size + b"}"


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def _refused_with_bounded_memory(path: Path, match: str) -> int:
    """``read_backup`` refuses the archive; returns the peak memory it allocated."""
    tracemalloc.start()
    try:
        with pytest.raises(RestoreError, match=match):
            read_backup(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def test_a_bomb_manifest_is_refused_before_it_is_inflated(tmp_path: Path) -> None:
    """Probe X3: a 64 KiB archive whose manifest.json unpacks to 64 MiB."""
    path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        with archive.open("manifest.json", "w") as handle:  # streamed: the test itself stays small
            handle.write(b"{")
            for _ in range(64):
                handle.write(b" " * MiB)
            handle.write(b"}")
        archive.writestr("data.json", b"{}")
    assert path.stat().st_size < 256 * 1024
    peak = _refused_with_bounded_memory(path, r"manifest\.json would unpack to 67,108,866 bytes")
    assert peak < 4 * MiB, f"{peak / MiB:.1f} MiB allocated"


def test_a_data_json_over_the_cap_is_refused_before_it_is_inflated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "backup_max_data_bytes", 2 * MiB)
    manifest = b'{"format": "disciplineos-backup", "format_version": 1, "files": {"data.json": {}}}'
    path = _zip(tmp_path / "big.zip", {"manifest.json": manifest, "data.json": _blank_json(3 * MiB)})
    peak = _refused_with_bounded_memory(path, r"data\.json would unpack to 3,145,730 bytes.*BACKUP_MAX_DATA_BYTES")
    assert peak < MiB
    # the archive itself larger than any backup can be: refused before it is even opened
    monkeypatch.setattr(get_settings(), "backup_max_data_bytes", 1024)
    (tmp_path / "huge.zip").write_bytes(b"PK" + b"\0" * (3 * MiB))
    _refused_with_bounded_memory(tmp_path / "huge.zip", "larger than any backup may be")


def test_a_data_json_that_deflates_a_thousand_times_is_refused(tmp_path: Path) -> None:
    manifest = b'{"format": "disciplineos-backup", "format_version": 1, "files": {"data.json": {}}}'
    path = _zip(tmp_path / "ratio.zip", {"manifest.json": manifest, "data.json": _blank_json(64 * MiB)})
    assert path.stat().st_size < 256 * 1024
    peak = _refused_with_bounded_memory(path, r"data\.json is compressed [\d,]+× — far more than any export")
    assert peak < 4 * MiB


def test_a_header_that_lies_about_the_size_never_inflates_past_it(tmp_path: Path) -> None:
    """The central directory says 1 KiB, the stream holds 8 MiB: reading stops at
    1 KiB and the CRC check refuses the member — the 8 MiB never reach memory."""
    manifest = b'{"format": "disciplineos-backup", "format_version": 1, "files": {"data.json": {}}}'
    raw = _zip(tmp_path / "honest.zip", {"manifest.json": manifest, "data.json": _blank_json(8 * MiB)}).read_bytes()
    entry = raw.rindex(b"PK\x01\x02")  # data.json's central directory entry (written last)
    assert raw[entry + 46 : entry + 46 + 9] == b"data.json"
    lying = raw[: entry + 24] + struct.pack("<I", 1024) + raw[entry + 28 :]  # "uncompressed size"
    (tmp_path / "lying.zip").write_bytes(lying)
    assert zipfile.ZipFile(tmp_path / "lying.zip").getinfo("data.json").file_size == 1024
    peak = _refused_with_bounded_memory(tmp_path / "lying.zip", "the archive is damaged: Bad CRC-32|checksum mismatch")
    assert peak < 4 * MiB


def test_a_corrupt_deflate_stream_is_a_clean_refusal(tmp_path: Path) -> None:
    manifest = b'{"format": "disciplineos-backup", "format_version": 1, "files": {"data.json": {}}}'
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest)
        archive.writestr("data.json", b"".join(b'{"n": %d},' % index for index in range(200_000)))
    raw = bytearray(data.getvalue())
    start = raw.index(b"data.json") + len("data.json")  # the local header's name, then the deflate stream
    raw[start + 5000 : start + 5400] = b"\xff" * 400
    (tmp_path / "corrupt.zip").write_bytes(bytes(raw))
    with pytest.raises(RestoreError, match=r"the archive is damaged|the archive cannot be read|checksum mismatch"):
        read_backup(tmp_path / "corrupt.zip")
