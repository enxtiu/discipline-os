"""Stage-8 review fixes on the restore side (review 8: M2, M3, L1, L2).

M3 — the manifest is signed (HMAC-SHA256, key by HKDF from ``BACKUP_SIGNING_KEY`` or
``SECRET_KEY``) over everything in it. A signed backup restores as before; any other
(edited, another server, a changed key) only into an empty account named with
``--user-id`` — the manifest's ``user_id`` never chooses an account — and the engine
rebuilds its XP, scores, streaks, achievements and quests from the records. Values
are re-checked in every case: the settings API's bounds and safety rule (an
unconfirmed floor or target below the floor is put back to the floor), and derived
rows of a signed backup within what the engine awards.

M2 — after remapping, every foreign key of a backup row points at a row of the
target account (rows of the backup restored before are the target's by now) or a
library row; a row of another account aborts the restore before anything is written.
Server paths are never taken from a backup: a meal keeps its thumbnail only when the
path is exactly the one built for that meal of the target account and the file is
there — no ``has_photo: true`` without a picture, no way to read or delete another
account's file.

L2 — restored JSON columns have the shape the app's readers rely on (an object or
an array as the column's default, never ``null``; lists of text, reminder
definitions, a score breakdown of numbers in range …): anything else is refused,
so a restored row never turns a page into a 500.

L1 — a backup archive is checked before anything in it is inflated: the declared
sizes (``manifest.json`` ≤ 1 MiB, ``data.json`` ≤ ``BACKUP_MAX_DATA_BYTES``), the
compression ratio, and reading stops at the limit even when a header lies — a tiny
archive never turns into hundreds of MiB of memory.
"""

from __future__ import annotations

import copy
import io
import struct
import tracemalloc
import uuid
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import orjson
import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hkdf_sha256
from app.models import MealItem, MealLog, Recipe, Streak, Task, User, UserSettings
from app.services import export, media, restore
from app.services.export import SECTIONS
from app.services.restore import RestoreError, Signature, _foreign_keys, check_signature, read_backup
from tests.stage6_helpers import other_user
from tests.stage8_helpers import (
    NOON,
    TODAY,
    YESTERDAY,
    _fresh_seed_cache,  # noqa: F401  (autouse fixture)
    configure,
    eat_day,
    local,
    meal,
    run,
    xp_rows,
    xp_total,
)
from tests.test_stage8_export import (
    cli,
    counts,
    gamify,
    populate,
    restore_file,
    rewrite_backup,
    save_backup,
    wipe,
)

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


# --------------------------------------------------------------------------- #
# M3 — a signed manifest; values re-checked, never trusted
# --------------------------------------------------------------------------- #
def test_hkdf_sha256_matches_rfc_5869() -> None:
    ikm = bytes.fromhex("0b" * 22)
    case_1 = hkdf_sha256(ikm, salt=bytes(range(13)), info=bytes(range(0xF0, 0xFA)), length=42)
    assert case_1.hex() == ("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865")
    case_3 = hkdf_sha256(ikm, salt=b"", info=b"", length=42)  # no salt: HashLen zeros
    assert case_3.hex() == ("8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d9d201395faa4b61a96c8")


async def test_the_signature_covers_the_whole_manifest(authed, tmp_path) -> None:
    path = await save_backup(authed, tmp_path / "a.zip")
    manifest = orjson.loads(zipfile.ZipFile(path).read("manifest.json"))
    assert check_signature(manifest) == Signature(True)
    assert get_settings().secret_key not in str(manifest) and export.signing_key().hex() not in str(manifest)
    for change in (
        lambda m: m.update(user_id=str(uuid.uuid4())),
        lambda m: m.update(schema_version="0002"),
        lambda m: m.update(created_at="2020-01-01T00:00:00+00:00"),
        lambda m: m["files"]["data.json"].update(sha256="0" * 64),
        lambda m: m["files"]["data.json"].update(bytes=1),
        lambda m: m["counts"].update(meal_logs=5),
        lambda m: m.update(timezone="UTC", app_version="9.9.9"),
        lambda m: m.update(extra=True),
    ):
        edited = copy.deepcopy(manifest)
        change(edited)
        assert check_signature(edited) == Signature(
            False, "its signature does not match: it was changed after it was made"
        )
    unsigned = {key: value for key, value in manifest.items() if key != "signature"}
    assert check_signature(unsigned) == Signature(False, "it carries no signature")
    for signature in ({**manifest["signature"], "alg": "none"}, "deadbeef", {"alg": "HMAC-SHA256"}):
        assert "not one this server knows" in str(check_signature({**manifest, "signature": signature}).problem)


async def test_backup_signing_key_secret_key_and_another_server(authed, owner, db, tmp_path, monkeypatch) -> None:
    """BACKUP_SIGNING_KEY signs when set (a SECRET_KEY rotation leaves its backups valid);
    a backup signed with another key counts as unsigned: refused without --user-id."""
    settings = get_settings()
    by_secret = await save_backup(authed, tmp_path / "secret.zip")
    monkeypatch.setattr(settings, "backup_signing_key", "backup-key-shared-by-both-servers-0123")
    shared = await save_backup(authed, tmp_path / "shared.zip")
    assert read_backup(shared).signed and not read_backup(by_secret).signed
    assert (
        read_backup(by_secret).signature.problem == "it was signed by another server, or before the signing key changed"
    )

    monkeypatch.setattr(settings, "secret_key", "a-rotated-secret-key-0123456789-abcdef")
    assert read_backup(shared).signed  # the backup key did not change

    monkeypatch.setattr(settings, "backup_signing_key", "the-key-of-another-server-0123456789")
    elsewhere = read_backup(shared)
    assert not elsewhere.signed and "another server" in str(elsewhere.signature.problem)
    with pytest.raises(RestoreError, match="this server did not sign the backup \\(it was signed by another server"):
        await restore_file(shared)

    monkeypatch.setattr(settings, "backup_signing_key", "backup-key-shared-by-both-servers-0123")
    report = await restore_file(shared)  # the same key again: a backup of this server
    assert report.signed and report.rebuilt == 0 and report.inserted == 0


async def _b_with_history(db, b_id: uuid.UUID) -> None:
    await configure(db, b_id)  # a man: target 2000 kcal, safe floor = the default 1500
    for day in (YESTERDAY, TODAY):
        await eat_day(db, b_id, day)
    await run(db, b_id, NOON)


def _gift(document: dict[str, Any], manifest: dict[str, Any]) -> None:
    gift = dict(document["tables"]["xp_events"][0])
    gift.update(id=str(uuid.uuid4()), kind="manual", points=10**9, dedupe_key="gift", reason="gift")
    document["tables"]["xp_events"].append(gift)
    manifest["counts"]["xp_events"] += 1


def _low_floor(document: dict[str, Any], manifest: dict[str, Any]) -> None:
    document["tables"]["settings"][0].update(
        safe_floor_kcal=1000, target_kcal=1000, targets_manual=True, safe_floor_confirmed_at=None
    )


async def _settings_of(db, user_id: uuid.UUID) -> tuple[Any, ...]:
    db.expire_all()
    row = (await db.scalars(select(UserSettings).where(UserSettings.user_id == user_id))).one()
    return row.target_kcal, row.safe_floor_kcal, row.safe_floor_confirmed_at, row.timezone


async def test_probe_x2_an_edited_backup_is_refused_and_a_signed_one_is_checked(owner, db, tmp_path) -> None:
    """Probe X2: 10^9 XP and an unconfirmed 1 000 kcal floor. Edited: refused. Signed
    with those values (a key holder, a bug): the XP is refused by its bound; the floor
    and the target are put back to the default floor (1 500 kcal for a man), reported —
    and kept only when confirmed, as the settings API does."""
    async with other_user() as b_client:
        b_id = await _b_id(db)
        await _b_with_history(db, b_id)
        original = await save_backup(b_client, tmp_path / "b.zip")
    before = (await xp_total(db, b_id), await _settings_of(db, b_id))
    assert before[1][:3] == (2000, None, None)

    def both(document: dict[str, Any], manifest: dict[str, Any]) -> None:
        _gift(document, manifest)
        _low_floor(document, manifest)

    with pytest.raises(RestoreError, match="this server did not sign the backup"):
        await restore_file(rewrite_backup(original, tmp_path / "edited.zip", both))
    with pytest.raises(RestoreError, match=r"xp_events\.points: 1000000000 is outside 0\.\.1000 "):
        await restore_file(rewrite_backup(original, tmp_path / "gift.zip", both, sign=True))
    assert (await xp_total(db, b_id), await _settings_of(db, b_id)) == before

    report = await restore_file(rewrite_backup(original, tmp_path / "floor.zip", _low_floor, sign=True))
    assert (await _settings_of(db, b_id))[:3] == (1500, None, None)
    assert report.notes == [
        "settings: a safe floor of 1000 kcal, below the default 1500 kcal, was never confirmed — the default applies",
        "settings: a calorie target of 1000 kcal, below the safe floor 1500 kcal, was never confirmed — set to 1500 kcal",
    ]

    def confirmed(document: dict[str, Any], manifest: dict[str, Any]) -> None:
        _low_floor(document, manifest)
        document["tables"]["settings"][0]["safe_floor_confirmed_at"] = "2026-07-01T10:00:00+00:00"

    report = await restore_file(rewrite_backup(original, tmp_path / "confirmed.zip", confirmed, sign=True))
    assert report.notes == [] and (await _settings_of(db, b_id))[:2] == (1000, 1000)
    assert await xp_total(db, b_id) == before[0]


async def test_probe_x2b_the_manifest_never_chooses_the_account(owner, db, tmp_path) -> None:
    """Probe X2b: B's backup with A's id in the manifest (and fresh row ids) — without
    --user-id refused; into A, which holds records of its own, refused too."""
    await configure(db, owner.id)  # A: Bangkok, target 2000, floor = the default
    async with other_user() as b_client:
        original = await save_backup(b_client, tmp_path / "b.zip")

    def retarget(document: dict[str, Any], manifest: dict[str, Any]) -> None:
        for name in list(document["tables"]):
            if name != "settings":
                document["tables"][name] = []
                manifest["counts"][name] = 0
        document["tables"]["settings"][0].update(
            id=str(uuid.uuid4()), safe_floor_kcal=1000, target_kcal=1000, targets_manual=True, timezone="America/Adak"
        )
        document["user_id"] = manifest["user_id"] = str(owner.id)

    crafted = rewrite_backup(original, tmp_path / "retarget.zip", retarget)
    with pytest.raises(RestoreError, match=r"this server did not sign the backup .* named with --user-id"):
        await restore_file(crafted)
    await meal(db, owner.id, TODAY, 600)
    with pytest.raises(RestoreError, match=rf"account {owner.id} is not empty \(meal_logs\): a backup this server"):
        await restore_file(crafted, user_id=owner.id)
    assert await _settings_of(db, owner.id) == (2000, None, None, "Asia/Bangkok")


async def test_an_unsigned_backup_goes_into_an_empty_account_and_the_engine_rebuilds_the_rest(
    owner, db, tmp_path
) -> None:
    """An edited backup (a forged 10^9 XP row and a 500-day streak): not into its own
    account, even with --user-id; into an empty one with --user-id the records go in,
    the derived rows do not — and the engine's next run rebuilds exactly the XP the
    source account had, without the forgeries."""
    async with other_user() as b_client:
        b_id = await _b_id(db)
        await _b_with_history(db, b_id)
        original = await save_backup(b_client, tmp_path / "b.zip")
    source_xp = {key: row.points for key, row in (await xp_rows(db, b_id)).items()}
    source_counts = await counts(db, b_id)
    assert source_xp and source_counts["xp_events"] == len(source_xp)

    def forged(document: dict[str, Any], manifest: dict[str, Any]) -> None:
        _gift(document, manifest)
        for row in document["tables"]["streaks"]:
            row.update(current_count=500, longest_count=500)

    unsigned = rewrite_backup(original, tmp_path / "unsigned.zip", forged)
    with pytest.raises(RestoreError, match=rf"account {b_id} is not empty .* did not sign"):
        await restore_file(unsigned, user_id=b_id)

    await db.execute(sa.delete(User).where(User.id == b_id))  # B gone (another server): A, empty, takes it
    await db.commit()
    dry = cli(str(unsigned), "--user-id", str(owner.id), "--dry-run")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "signature: not valid here — its signature does not match" in dry.stdout
    assert "rebuilt by the engine" in dry.stdout and "left to the engine" in dry.stdout
    assert "the gamification engine rebuilds them from the restored records" in dry.stdout

    report = await restore_file(unsigned, user_id=owner.id)
    assert (
        not report.signed
        and report.signature_problem == "its signature does not match: it was changed after it was made"
    )
    derived = [section.name for section in SECTIONS if section.derived]
    assert {name: report.tables[name].rebuilt for name in derived} == {
        name: source_counts[name] + (name == "xp_events") for name in derived
    }
    assert all(report.tables[name].changed == 0 for name in derived)
    restored = await counts(db, owner.id)
    records = [section.name for section in SECTIONS if section.restore and not section.in_empty_account]
    assert {name: restored[name] for name in records} == {name: source_counts[name] for name in records}
    assert await xp_total(db, owner.id) == 0  # nothing derived was taken from the file
    assert 500 not in (await db.scalars(select(Streak.current_count).where(Streak.user_id == owner.id))).all()

    await run(db, owner.id, NOON)  # the engine's next run: the same history gives the same XP
    rebuilt = {key: row.points for key, row in (await xp_rows(db, owner.id)).items()}
    assert rebuilt == source_xp and "gift" not in rebuilt
    assert max((await db.scalars(select(Streak.current_count).where(Streak.user_id == owner.id))).all()) < 10
    with pytest.raises(RestoreError, match=rf"account {owner.id} is not empty"):  # A holds records now
        await restore_file(unsigned, user_id=owner.id)


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("target_kcal", 500, r"settings\.target_kcal: 500 is outside 1000\.\.20000"),
        ("safe_floor_kcal", 900, r"settings\.safe_floor_kcal: 900 is outside 1000\.\.3500"),
        ("week_start", 9, r"settings\.week_start: 9 is outside 0\.\.6"),
        ("weight_kg", 1000, r"settings\.weight_kg: 1000 is outside 20\.\.400"),
        ("target_water_ml", -5, r"settings\.target_water_ml: -5 is outside 0\.\.20000"),
        ("units", "imperial", r"settings\.units: 'imperial' — the app is metric only"),
        ("xp_weights", {"score_weights": {"kcal": 500}}, r"settings\.xp_weights: score weight kcal must be"),
        ("xp_weights", {"xp_multipliers": {"nutrition": 50}}, r"settings\.xp_weights: XP multiplier nutrition"),
        ("xp_weights", [], r"settings\.xp_weights: bad value \[\] \(a JSON object is required\)"),
    ],
)
async def test_settings_the_app_never_stores_are_refused(authed, db, tmp_path, column, value, message) -> None:
    path = await save_backup(authed, tmp_path / "a.zip")

    def change(document: dict[str, Any], manifest: dict[str, Any]) -> None:
        document["tables"]["settings"][0][column] = value

    with pytest.raises(RestoreError, match=message):
        await restore_file(rewrite_backup(path, tmp_path / "changed.zip", change, sign=True))


@pytest.mark.parametrize(
    ("table", "values", "message"),
    [
        ("xp_events", {"points": -1}, r"xp_events\.points: -1 is outside 0\.\.1000"),
        ("daily_scores", {"xp_earned": 2_000_000_000}, r"daily_scores\.xp_earned: 2000000000 is outside"),
        ("daily_scores", {"score": 101}, r"daily_scores\.score: 101 is outside 0\.\.100"),
        ("streaks", {"current_count": 10**6}, r"streaks\.current_count: 1000000 is outside 0\.\.100000"),
        ("streaks", {"freeze_tokens": 7}, r"streaks\.freeze_tokens: 7 is outside 0\.\.2"),
        ("user_quests", {"period_end": "2026-07-01"}, r"user_quests: period 2026-07-1\d\.\.2026-07-01 ends before"),
        ("xp_events", {"kind": "free_points"}, r"xp_events\.kind: bad value 'free_points'"),
    ],
)
async def test_derived_rows_of_a_signed_backup_are_checked(owner, db, tmp_path, table, values, message) -> None:
    async with other_user() as b_client:
        await _b_with_history(db, await _b_id(db))
        path = await save_backup(b_client, tmp_path / "b.zip")

    def change(document: dict[str, Any], manifest: dict[str, Any]) -> None:
        document["tables"][table][0].update(values)

    with pytest.raises(RestoreError, match=message):
        await restore_file(rewrite_backup(path, tmp_path / "changed.zip", change, sign=True))


def test_the_bounds_cover_every_award_the_engine_makes() -> None:
    """An honest signed backup never trips them: they sit at or above the engine's maxima."""
    from app.schemas.gamification import MAX_XP_MULTIPLIER
    from app.seeds.gamification import ACHIEVEMENTS, QUESTS
    from app.services.gamification.streaks import MAX_FREEZE_TOKENS, OVERALL_MILESTONES

    largest = max(
        [points for _, points in OVERALL_MILESTONES] + [item.xp for item in ACHIEVEMENTS] + [item.xp for item in QUESTS]
    )
    assert largest * MAX_XP_MULTIPLIER <= restore.MAX_EVENT_POINTS
    assert restore.MAX_FREEZE_TOKENS == MAX_FREEZE_TOKENS


# --------------------------------------------------------------------------- #
# L2 — JSON columns: the shapes the app's readers rely on
# --------------------------------------------------------------------------- #
async def test_probe_x4_a_malformed_score_breakdown_never_reaches_the_scores_endpoint(owner, db, tmp_path) -> None:
    """Probe X4: a frozen day's ``components.items[0].value = "n/a"``. Edited: refused
    (M3); signed: refused by its shape — ``GET /gamification/scores`` stays a 200."""
    frozen_day = TODAY - timedelta(days=5)
    async with other_user() as b_client:
        b_id = await _b_id(db)
        await configure(db, b_id)
        await meal(db, b_id, frozen_day, 1800)
        await run(db, b_id, NOON)  # frozen_day is finalised (and frozen) by this run
        original = await save_backup(b_client, tmp_path / "b.zip")

        def damage(document: dict[str, Any], manifest: dict[str, Any]) -> None:
            for row in document["tables"]["daily_scores"]:
                if row["local_day"] == frozen_day.isoformat():
                    row["components"]["items"][0]["value"] = "n/a"

        with pytest.raises(RestoreError, match="this server did not sign the backup"):
            await restore_file(rewrite_backup(original, tmp_path / "damaged.zip", damage))
        with pytest.raises(
            RestoreError,
            match=r"daily_scores\.components: items\[kcal\]\.value must be a number in 0\.\.1 — not a shape",
        ):
            await restore_file(rewrite_backup(original, tmp_path / "signed.zip", damage, sign=True))
        params = {"from": (frozen_day - timedelta(days=1)).isoformat(), "to": frozen_day.isoformat()}
        response = await b_client.get("/api/v1/gamification/scores", params=params)
        assert response.status_code == 200, response.text


SHAPE_CASES: list[tuple[str, str, Any, str]] = [
    ("settings", "notification_prefs", [], r"settings\.notification_prefs: bad value \[\] \(a JSON object"),
    ("settings", "notification_prefs", None, r"settings\.notification_prefs: bad value None \(a value is required"),
    ("settings", "extras", "x", r"settings\.extras: bad value 'x' \(a JSON object is required"),
    ("foods", "aliases", ["сырники", 7], r"foods\.aliases: every item must be text"),
    ("saved_meal_templates", "items", ["2 eggs"], r"saved_meal_templates\.items: every item must be an object"),
    ("exercises", "secondary_muscles", {"chest": 1}, r"exercises\.secondary_muscles: bad value .*a JSON array"),
    ("health_daily", "raw_payload", [1, 2], r"health_daily\.raw_payload: bad value \[1, 2\] \(a JSON object"),
    ("projects", "external_meta", None, r"projects\.external_meta: bad value None"),
    ("resources", "tags", [None], r"resources\.tags: every item must be text"),
    ("tasks", "tags_snapshot", "важное", r"tasks\.tags_snapshot: bad value 'важное' \(a JSON array"),
    ("journal_entries", "tags", [["nested"]], r"journal_entries\.tags: every item must be text"),
    ("reminders", "payload", {"lead_minutes": "soon"}, r"reminders\.payload: lead_minutes must be a whole number"),
    ("reminders", "payload", {"lead_minutes": 10_081}, r"reminders\.payload: lead_minutes must be"),
    ("reminders", "payload", {"date": "tomorrow", "time": "09:00"}, r"reminders\.payload: Invalid isoformat"),
    ("reminders", "payload", {"date": "2026-07-14", "time": "25:00"}, r"reminders\.payload: time must be HH:MM"),
    ("xp_events", "meta", [], r"xp_events\.meta: bad value \[\] \(a JSON object"),
    ("streaks", "freeze_used_dates", ["yesterday"], r"streaks\.freeze_used_dates: Invalid isoformat"),
    ("streaks", "freeze_used_dates", [20260714], r"streaks\.freeze_used_dates: every item must be a YYYY-MM-DD"),
    ("daily_scores", "components", {"credit": "true"}, r"daily_scores\.components: credit must be true or false"),
    ("daily_scores", "components", {"items": {"kcal": 1}}, r"daily_scores\.components: items must be an array"),
    ("daily_scores", "components", {"items": [{"value": 1}]}, r"daily_scores\.components: every item must be an"),
    (
        "daily_scores",
        "components",
        {"items": [{"key": "water", "value": 0.5, "weight": 10, "detail": "ok"}]},
        r"daily_scores\.components: items\[water\]\.detail must be an object",
    ),
    ("daily_scores", "weights", {"kcal": "25"}, r"daily_scores\.weights: kcal must be a number in 0\.\.100"),
]


async def test_json_columns_of_a_shape_the_app_never_stores_are_refused(authed, owner, db, tmp_path) -> None:
    """One account with a row in every table (and the engine's rows), one malformed JSON
    value at a time — in a backup this server signed, so the check itself is reached."""
    await configure(db, owner.id)
    await populate(db, owner.id)
    await gamify(db, owner.id)
    original = await save_backup(authed, tmp_path / "a.zip")
    before = await counts(db, owner.id)
    for index, (table, column, value, message) in enumerate(SHAPE_CASES):

        def change(document: dict[str, Any], manifest: dict[str, Any], table=table, column=column, value=value) -> None:
            document["tables"][table][0][column] = value

        with pytest.raises(RestoreError, match=message):
            await restore_file(rewrite_backup(original, tmp_path / f"{index}.zip", change, sign=True))
    assert await counts(db, owner.id) == before

    def tolerated(document: dict[str, Any], manifest: dict[str, Any]) -> None:
        # what PATCH /settings itself accepts (any object; the reader falls back key by key) and
        # a score breakdown with a key a newer engine may add
        document["tables"]["settings"][0]["notification_prefs"] = {"meal_times": "09:00", "legacy_flag": 1}
        document["tables"]["daily_scores"][0]["components"]["final"] = True

    report = await restore_file(rewrite_backup(original, tmp_path / "tolerated.zip", tolerated, sign=True))
    assert report.tables["settings"].updated == 1 and report.tables["daily_scores"].updated == 1
    from app.services.notifications import DEFAULT_MEAL_TIMES

    prefs = await authed.get("/api/v1/notifications/prefs")
    assert prefs.status_code == 200 and prefs.json()["meal_times"] == list(DEFAULT_MEAL_TIMES)  # the reader's fallback
