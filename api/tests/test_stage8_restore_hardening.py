"""Stage-8 review fixes on the restore side (review 8: M2, M3, L1, L2).

M2 — after remapping, every foreign key of a backup row points at a row of the
target account (rows of the backup restored before are the target's by now) or a
library row; a row of another account aborts the restore before anything is written.
Server paths are never taken from a backup: a meal keeps its thumbnail only when the
path is exactly the one built for that meal of the target account and the file is
there — no ``has_photo: true`` without a picture, no way to read or delete another
account's file.

L1 — a backup archive is checked before anything in it is inflated: the declared
sizes (``manifest.json`` ≤ 1 MiB, ``data.json`` ≤ ``BACKUP_MAX_DATA_BYTES``), the
compression ratio, and reading stops at the limit even when a header lies — a tiny
archive never turns into hundreds of MiB of memory.
"""

from __future__ import annotations

import io
import struct
import tracemalloc
import uuid
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.core.config import get_settings
from app.models import MealItem, MealLog, Recipe, Task, User
from app.services import media
from app.services.export import SECTIONS
from app.services.restore import RestoreError, _foreign_keys, read_backup
from tests.stage6_helpers import other_user
from tests.stage8_helpers import _fresh_seed_cache, configure, local  # noqa: F401  (autouse fixture)
from tests.test_stage8_export import counts, populate, restore_file, rewrite_backup, save_backup, wipe

pytestmark = pytest.mark.anyio
MiB = 1 << 20
DAY = date(2026, 7, 14)


async def _b_id(db) -> uuid.UUID:
    return (await db.scalars(select(User.id).where(User.is_owner.is_(False)))).one()


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


# --------------------------------------------------------------------------- #
# M2 — foreign keys and server paths never reach into another account
# --------------------------------------------------------------------------- #
async def _owned_row(db, table: sa.Table, user_id: uuid.UUID) -> Any:
    """One id of a row of ``user_id`` in ``table`` (its own, never a library row)."""
    stmt = select(table.c.id).where(table.c.user_id == user_id)
    if "is_global" in table.c:
        stmt = stmt.where(table.c.is_global.is_(False))
    return (await db.execute(stmt.limit(1))).scalar()


async def test_no_foreign_key_of_a_backup_row_reaches_into_another_account(owner, db, tmp_path) -> None:
    """Every foreign key column of every restored table, one at a time: B's own backup
    with one row pointing at A's row is refused, and nothing of A or B changes. The
    backups are signed again (as if this server had made them so): an edited one is
    refused earlier, by its signature, and would never show that this check holds."""
    await configure(db, owner.id)
    await populate(db, owner.id)  # A: a row in every table
    async with other_user() as b_client:
        b_id = await _b_id(db)
        await configure(db, b_id)
        await populate(db, b_id, tag=" B")
        original = await save_backup(b_client, tmp_path / "b.zip")
    before = (await counts(db, owner.id), await counts(db, b_id))
    checked = []
    for section in SECTIONS:
        if not section.restore:
            continue
        for column, referenced in _foreign_keys(section).items():
            table = section.table.metadata.tables[referenced]
            if "user_id" not in table.c:
                continue  # achievements: library rows only
            victim = await _owned_row(db, table, owner.id)
            if victim is None:
                continue  # quests: A owns none (library rows)

            def point(document: dict[str, Any], manifest: dict[str, Any], name=section.name, column=column, to=victim):
                document["tables"][name][0][column] = str(to)

            crafted = rewrite_backup(original, tmp_path / f"{section.name}-{column}.zip", point, sign=True)
            with pytest.raises(
                RestoreError, match=rf"{section.name}\.{column}: {referenced} {victim} belongs to another"
            ):
                await restore_file(crafted)
            checked.append(f"{section.name}.{column}")
    assert len(checked) == 29, checked
    assert (await counts(db, owner.id), await counts(db, b_id)) == before
    assert (await restore_file(original)).inserted == 0  # the untouched backup is fine


async def test_probe_x1_an_injected_item_never_shows_in_another_accounts_meal(authed, owner, db, tmp_path) -> None:
    """Probe X1: B's backup with a meal item inside A's meal and a child of A's task —
    refused as an edited backup (M3); signed, refused by its foreign keys (M2); and
    unsigned into an empty account (the way an edited backup may go in), refused too."""
    a_meal = MealLog(user_id=owner.id, logged_at=local(DAY, 8), local_day=DAY, kcal=Decimal(500), item_count=1)
    db.add(a_meal)
    await db.commit()
    db.add(MealItem(user_id=owner.id, meal_log_id=a_meal.id, name="A's own buckwheat", grams=Decimal(100)))
    task_row = Task(user_id=owner.id, title="A's private task")
    db.add(task_row)
    await db.commit()
    a_task = task_row.id
    async with other_user() as b_client:
        b_id = await _b_id(db)
        b_meal = MealLog(user_id=b_id, logged_at=local(DAY, 8), local_day=DAY, kcal=Decimal(400), item_count=1)
        db.add(b_meal)
        await db.commit()
        db.add_all([MealItem(user_id=b_id, meal_log_id=b_meal.id, name="B's toast", grams=Decimal(80))])
        db.add(Task(user_id=b_id, title="B's task"))
        await db.commit()
        original = await save_backup(b_client, tmp_path / "b.zip")

    def inject(document: dict[str, Any], manifest: dict[str, Any]) -> None:
        item = dict(document["tables"]["meal_items"][0])
        item.update(id=str(uuid.uuid4()), meal_log_id=str(a_meal.id), name="INJECTED BY B")
        document["tables"]["meal_items"].append(item)
        task = dict(document["tables"]["tasks"][0])
        task.update(id=str(uuid.uuid4()), client_uuid=str(uuid.uuid4()), parent_id=str(a_task), title="B's child")
        document["tables"]["tasks"].append(task)
        for name in ("meal_items", "tasks"):
            manifest["counts"][name] += 1

    crafted = rewrite_backup(original, tmp_path / "crafted.zip", inject)
    with pytest.raises(RestoreError, match="this server did not sign the backup"):
        await restore_file(crafted)
    foreign_key = r"meal_items\.meal_log_id: meal_logs .* belongs to another account"
    with pytest.raises(RestoreError, match=foreign_key):
        await restore_file(rewrite_backup(original, tmp_path / "signed.zip", inject, sign=True))
    await db.execute(sa.delete(User).where(User.id == b_id))  # B gone (another server): C, empty, takes it
    await db.commit()
    async with other_user():
        c_id = await _b_id(db)
        fresh = await counts(db, c_id)
        with pytest.raises(RestoreError, match=foreign_key):
            await restore_file(crafted, user_id=c_id)
        assert await counts(db, c_id) == fresh
    day = (await authed.get("/api/v1/nutrition/day", params={"date": DAY.isoformat()})).json()
    assert [item["name"] for meal_row in day["meals"] for item in meal_row["items"]] == ["A's own buckwheat"]
    db.expire_all()
    assert (await db.scalars(select(Task.id).where(Task.parent_id == a_task))).all() == []


@pytest.fixture
def media_dir(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "media"
    monkeypatch.setattr(get_settings(), "media_dir", str(folder))
    return folder


async def _meal_with_photo(db, user_id: uuid.UUID, picture: bytes | None) -> uuid.UUID:
    row = MealLog(user_id=user_id, logged_at=local(DAY, 13), local_day=DAY, kcal=Decimal(450), item_count=0)
    db.add(row)
    await db.commit()
    if picture is not None:
        row.photo_thumb_path = media.meal_thumb_relpath(user_id, row.id)
        media.write(row.photo_thumb_path, picture)
        await db.commit()
    return row.id


async def test_probe_x1b_a_photo_path_never_reads_or_deletes_another_accounts_file(
    authed, owner, db, tmp_path, media_dir
) -> None:
    """Probe X1b: B's meal restored with A's thumbnail path. Edited, the backup is refused
    (M3); signed, it restores without the path — B gets no photo, and deleting B's meal
    leaves A's file alone (M2)."""
    a_meal = await _meal_with_photo(db, owner.id, b"\xff\xd8\xff A's private photo")
    a_path = media.meal_thumb_relpath(owner.id, a_meal)
    async with other_user() as b_client:
        b_id = await _b_id(db)
        b_meal = await _meal_with_photo(db, b_id, None)
        original = await save_backup(b_client, tmp_path / "b.zip")

        def point(document: dict[str, Any], manifest: dict[str, Any]) -> None:
            for row in document["tables"]["meal_logs"]:
                if row["id"] == str(b_meal):
                    row["photo_thumb_path"] = a_path

        with pytest.raises(RestoreError, match="this server did not sign the backup"):
            await restore_file(rewrite_backup(original, tmp_path / "photo.zip", point))
        report = await restore_file(rewrite_backup(original, tmp_path / "signed.zip", point, sign=True))
        assert report.tables["meal_logs"].unchanged == 1  # the path never made it in: the row is as it was
        assert (await b_client.get(f"/api/v1/nutrition/meals/{b_meal}/photo")).status_code == 404
        day = (await b_client.get("/api/v1/nutrition/day", params={"date": DAY.isoformat()})).json()
        assert [meal["has_photo"] for meal in day["meals"]] == [False]
        assert (await b_client.delete(f"/api/v1/nutrition/meals/{b_meal}")).status_code == 200
    assert media.path_of(a_path) is not None
    mine = await authed.get(f"/api/v1/nutrition/meals/{a_meal}/photo")
    assert mine.status_code == 200 and mine.content == b"\xff\xd8\xff A's private photo"


async def test_a_thumbnail_survives_a_restore_only_where_its_file_is(owner, db, tmp_path, media_dir) -> None:
    """Same server, same account: the path the server built and the file is there →
    kept. The file gone (another server, a lost volume) → no photo. Into another
    (empty) account → no photo either: the path is built from the target's id."""
    async with other_user() as b_client:
        b_id = await _b_id(db)
        with_file = await _meal_with_photo(db, b_id, b"\xff\xd8\xff B's lunch")
        without_file = await _meal_with_photo(db, b_id, b"\xff\xd8\xff B's dinner")
        db.add(Recipe(user_id=b_id, name="Каша", photo_thumb_path=f"meals/{b_id}/{uuid.uuid4()}.jpg"))
        await db.commit()
        path = await save_backup(b_client, tmp_path / "b.zip")
        media.delete(media.meal_thumb_relpath(b_id, without_file))
        await wipe(db, b_id)

        await restore_file(path)
        db.expire_all()
        restored = {row.id: row.photo_thumb_path for row in (await db.scalars(select(MealLog))).all()}
        assert restored == {with_file: media.meal_thumb_relpath(b_id, with_file), without_file: None}
        assert (await db.scalars(select(Recipe.photo_thumb_path))).all() == [None]
        photo = await b_client.get(f"/api/v1/nutrition/meals/{with_file}/photo")
        assert photo.status_code == 200 and photo.content == b"\xff\xd8\xff B's lunch"
        day = (await b_client.get("/api/v1/nutrition/day", params={"date": DAY.isoformat()})).json()
        assert sorted(meal["has_photo"] for meal in day["meals"]) == [False, True]

    # B's account gone (as on another server, B's files still here): A's empty account takes the backup
    await db.execute(sa.delete(User).where(User.id == b_id))
    await db.commit()
    await restore_file(path, user_id=owner.id)
    db.expire_all()
    rows = (await db.scalars(select(MealLog).where(MealLog.user_id == owner.id))).all()
    assert len(rows) == 2 and all(row.photo_thumb_path is None for row in rows)
    assert media.path_of(media.meal_thumb_relpath(b_id, with_file)) is not None  # nobody's file was touched
