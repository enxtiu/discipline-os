"""Server-side CLI.

    python -m app.cli create-owner            # first run: creates owner + prints a pairing code
    python -m app.cli pairing-code            # new code for another device
    python -m app.cli api-token --name shortcut --scopes ingest:health,read:all
    python -m app.cli list-devices
    python -m app.cli revoke-device <uuid>
    python -m app.cli recompute-targets
    python -m app.cli run-job housekeeping
    python -m app.cli vapid-keys
    python -m app.cli stats

Run it inside the container:  docker compose exec api python -m app.cli create-owner
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import typer
from sqlalchemy import func, select

from app.core.logging import configure_logging, get_logger
from app.db import SessionLocal, ping
from app.models import ApiToken, Device, MealLog, Task, User, UserSettings, WorkoutLog
from app.services.bootstrap import create_owner
from app.services.pairing import create_pairing_code, revoke_device
from app.services.scheduler import run_job_now
from app.services.settings_service import get_or_create_settings, recompute_targets
from app.services.tokens import create_api_token

cli = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="DisciplineOS administration commands (run on the server).",
)
log = get_logger("app.cli")


def _run(coro):
    return asyncio.run(coro)


def _print_pairing_code(code: str, expires_at: datetime, max_uses: int) -> None:
    ttl_left = int((expires_at - datetime.now(UTC)).total_seconds() // 60)
    typer.echo("")
    typer.secho("  ┌──────────────────────────────────────────────┐", fg=typer.colors.CYAN)
    typer.secho("  │            DISCIPLINE OS · PAIRING            │", fg=typer.colors.CYAN)
    typer.secho("  └──────────────────────────────────────────────┘", fg=typer.colors.CYAN)
    typer.echo("")
    typer.secho(f"        {code}", fg=typer.colors.BRIGHT_WHITE, bold=True)
    typer.echo("")
    typer.echo(f"  valid for {ttl_left} min ({max_uses} device{'s' if max_uses > 1 else ''})")
    typer.echo("  open the app → enter the code → the device is paired")
    typer.echo("")


@cli.command("create-owner")
def create_owner_cmd(
    name: Annotated[str, typer.Option("--name", help="Display name")] = "Owner",
    email: Annotated[str | None, typer.Option("--email")] = None,
    max_devices: Annotated[int | None, typer.Option("--max-devices")] = None,
    language: Annotated[str | None, typer.Option("--language", help="ru | en")] = None,
    timezone_name: Annotated[str | None, typer.Option("--timezone", help="IANA tz, e.g. Asia/Bangkok")] = None,
    no_code: Annotated[bool, typer.Option("--no-code", help="Do not print a pairing code")] = False,
) -> None:
    """Create the owner account (idempotent) and print the first pairing code."""

    async def _inner() -> None:
        async with SessionLocal() as db:
            result = await create_owner(
                db,
                display_name=name,
                email=email,
                max_devices=max_devices,
                language=language,
                timezone=timezone_name,
                with_pairing_code=not no_code,
            )
            await db.commit()
            if result.created:
                typer.secho(f"✓ owner created: {name}", fg=typer.colors.GREEN)
            else:
                typer.secho("• owner already exists", fg=typer.colors.YELLOW)
            if result.pairing_code:
                _print_pairing_code(
                    result.pairing_code.code,
                    result.pairing_code.expires_at,
                    result.pairing_code.max_uses,
                )

    _run(_inner())


@cli.command("pairing-code")
def pairing_code_cmd(
    ttl: Annotated[int | None, typer.Option("--ttl", help="Minutes")] = None,
    uses: Annotated[int, typer.Option("--uses", help="How many devices may use it")] = 1,
) -> None:
    """Issue a new one-time pairing code."""

    async def _inner() -> None:
        async with SessionLocal() as db:
            user = (await db.execute(select(User).where(User.is_owner.is_(True)))).scalars().first()
            if user is None:
                typer.secho("✗ no owner yet — run `create-owner` first", fg=typer.colors.RED)
                raise typer.Exit(1)
            issued = await create_pairing_code(db, user, ttl_minutes=ttl, max_uses=uses, created_by="cli")
            await db.commit()
            _print_pairing_code(issued.code, issued.expires_at, issued.max_uses)

    _run(_inner())


@cli.command("api-token")
def api_token_cmd(
    name: Annotated[str, typer.Option("--name")] = "automation",
    scopes: Annotated[str, typer.Option("--scopes", help="Comma separated scopes")] = "read:all",
    expires_days: Annotated[int | None, typer.Option("--expires-days")] = None,
    description: Annotated[str | None, typer.Option("--description")] = None,
) -> None:
    """Create a scoped API token (printed once; store it in the Shortcut)."""

    async def _inner() -> None:
        async with SessionLocal() as db:
            user = (await db.execute(select(User).where(User.is_owner.is_(True)))).scalars().first()
            if user is None:
                typer.secho("✗ no owner yet — run `create-owner` first", fg=typer.colors.RED)
                raise typer.Exit(1)
            scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
            issued = await create_api_token(
                db,
                user,
                name=name,
                scopes=scope_list,
                description=description,
                expires_at=(datetime.now(UTC) + timedelta(days=expires_days) if expires_days else None),
            )
            await db.commit()
            typer.echo("")
            typer.secho(f"  {issued.token}", fg=typer.colors.BRIGHT_WHITE, bold=True)
            typer.echo("")
            typer.echo(f"  scopes: {', '.join(issued.row.scopes)}")
            typer.echo("  This value is shown only once.")

    _run(_inner())


@cli.command("list-devices")
def list_devices_cmd(
    all_devices: Annotated[bool, typer.Option("--all", help="Include revoked")] = False,
) -> None:
    """Show paired devices and their last-seen timestamps."""

    async def _inner() -> None:
        async with SessionLocal() as db:
            user = (await db.execute(select(User).where(User.is_owner.is_(True)))).scalars().first()
            if user is None:
                typer.secho("✗ no owner yet", fg=typer.colors.RED)
                raise typer.Exit(1)
            rows = (
                (await db.execute(select(Device).where(Device.user_id == user.id).order_by(Device.created_at)))
                .scalars()
                .all()
            )
            if not rows:
                typer.echo("no devices paired yet")
            for device in rows:
                if not all_devices and not device.is_active:
                    continue
                state = "active" if device.is_active else "revoked"
                last = device.last_seen_at.isoformat(sep=" ", timespec="minutes") if device.last_seen_at else "never"
                typer.echo(f"{device.id}  {state:8}  {device.name[:24]:24}  last seen: {last}")

    _run(_inner())


@cli.command("revoke-device")
def revoke_device_cmd(device_id: str) -> None:
    """Revoke one device (its token stops working immediately)."""

    async def _inner() -> None:
        async with SessionLocal() as db:
            user = (await db.execute(select(User).where(User.is_owner.is_(True)))).scalars().first()
            if user is None:
                typer.secho("✗ no owner yet", fg=typer.colors.RED)
                raise typer.Exit(1)
            try:
                device = await revoke_device(db, user, uuid.UUID(device_id))
            except Exception as exc:
                typer.secho(f"✗ {exc}", fg=typer.colors.RED)
                raise typer.Exit(1) from exc
            await db.commit()
            typer.secho(f"✓ revoked {device.name} ({device.id})", fg=typer.colors.GREEN)

    _run(_inner())


@cli.command("recompute-targets")
def recompute_targets_cmd() -> None:
    """Recompute calorie/macro targets from the current body parameters."""

    async def _inner() -> None:
        async with SessionLocal() as db:
            user = (await db.execute(select(User).where(User.is_owner.is_(True)))).scalars().first()
            if user is None:
                typer.secho("✗ no owner yet", fg=typer.colors.RED)
                raise typer.Exit(1)
            row = await get_or_create_settings(db, user)
            result = await recompute_targets(db, row, force=True)
            await db.commit()
            typer.echo(
                json.dumps(
                    {
                        "ok": result.ok,
                        "formula": result.formula_used,
                        "bmr_kcal": result.bmr_kcal,
                        "tdee_kcal": result.tdee_kcal,
                        "target_kcal": result.target_kcal,
                        "protein_g": result.target_protein_g,
                        "fat_g": result.target_fat_g,
                        "carbs_g": result.target_carbs_g,
                        "fiber_g": result.target_fiber_g,
                        "water_ml": result.target_water_ml,
                        "warnings": result.warnings,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )

    _run(_inner())


@cli.command("run-job")
def run_job_cmd(
    job_id: Annotated[str, typer.Argument(help="Job id, e.g. housekeeping")],
) -> None:
    """Run a scheduler job once, right now (handy for debugging)."""

    async def _inner() -> None:
        found = await run_job_now(job_id)
        if found:
            typer.secho(f"✓ job '{job_id}' finished", fg=typer.colors.GREEN)
        else:
            typer.secho(f"✗ unknown job '{job_id}'", fg=typer.colors.RED)
            raise typer.Exit(1)

    _run(_inner())


@cli.command("seed")
def seed_cmd(
    names: Annotated[
        list[str] | None,
        typer.Argument(help="Seed name(s); omit to run every seed. Use --list to see them."),
    ] = None,
    list_only: Annotated[bool, typer.Option("--list", "-l", help="Show the available seeds and exit")] = False,
) -> None:
    """Seed reference data (exercise library, …). Idempotent — safe to re-run."""
    from app.seeds import list_seeds, run_seeds

    if list_only:
        for seed in list_seeds():
            tags = f" [{', '.join(seed.tags)}]" if seed.tags else ""
            typer.echo(f"{seed.name:14} {seed.description}{tags}")
        return

    async def _inner() -> None:
        if not await ping():
            typer.secho("✗ database unreachable", fg=typer.colors.RED)
            raise typer.Exit(1)
        async with SessionLocal() as db:
            try:
                report = await run_seeds(db, names)
            except KeyError as exc:
                typer.secho(f"✗ {exc.args[0]}", fg=typer.colors.RED)
                raise typer.Exit(1) from None
        for name, counts in report.items():
            rendered = ", ".join(f"{key}={value}" for key, value in counts.items())
            typer.secho(f"✓ {name}: {rendered}", fg=typer.colors.GREEN)

    _run(_inner())


@cli.command("import-foods")
def import_foods_cmd(
    source: Annotated[str, typer.Option("--source", help="seed | off | usda")] = "seed",
    file: Annotated[str | None, typer.Option("--file", help="JSONL (off) / CSV (usda) path")] = None,
    limit: Annotated[int | None, typer.Option("--limit", help="Stop after N source rows")] = None,
) -> None:
    """Import foods into the global library (idempotent by source+id)."""
    from app.importers import off, seed, usda
    from app.importers.base import run_import
    from app.models import FoodSource

    if source == "seed":
        rows, src = seed.iterate_seed(file), FoodSource.SEED
    elif source == "off":
        if not file:
            raise typer.BadParameter("--file is required for the off source")
        rows, src = off.iterate_off(file), FoodSource.OFF
    elif source == "usda":
        if not file:
            raise typer.BadParameter("--file is required for the usda source")
        rows, src = usda.iterate_usda(file), FoodSource.USDA
    else:
        raise typer.BadParameter(f"unknown source: {source}")

    async def _inner():
        async with SessionLocal() as db:
            stats = await run_import(db, rows, source=src, limit=limit)
            await db.commit()
            return stats

    stats = _run(_inner())
    typer.secho(
        f"✓ {stats.source}: seen={stats.seen} created={stats.created} updated={stats.updated} skipped={stats.skipped}",
        fg=typer.colors.GREEN,
    )


@cli.command("vapid-keys")
def vapid_keys_cmd() -> None:
    """Generate a VAPID key pair for Web Push (stage 7)."""
    try:
        from py_vapid import Vapid02  # type: ignore
    except ImportError:  # pragma: no cover - optional dependency
        typer.secho("✗ py-vapid is not installed", fg=typer.colors.RED)
        raise typer.Exit(1) from None

    vapid = Vapid02()
    vapid.generate_keys()
    private_pem = vapid.private_pem().decode()
    public_key = vapid.public_key_urlsafe_base64()
    typer.echo("Add these to .env and restart the api container:\n")
    typer.secho(f"VAPID_PUBLIC_KEY={public_key}", fg=typer.colors.GREEN)
    typer.echo("VAPID_PRIVATE_KEY=" + private_pem.replace("\n", "\\n"))
    typer.echo("\n(keep the private key secret — it never belongs in the repo)")


@cli.command("stats")
def stats_cmd() -> None:
    """Quick row counts — sanity check after a deploy or restore."""

    async def _inner() -> None:
        if not await ping():
            typer.secho("✗ database unreachable", fg=typer.colors.RED)
            raise typer.Exit(1)
        async with SessionLocal() as db:
            for model, label in (
                (User, "users"),
                (Device, "devices"),
                (ApiToken, "api_tokens"),
                (MealLog, "meal_logs"),
                (WorkoutLog, "workout_logs"),
                (Task, "tasks"),
            ):
                count = await db.scalar(select(func.count()).select_from(model))
                typer.echo(f"{label:14} {count}")
            settings_row = (await db.execute(select(UserSettings))).scalars().first()
            if settings_row:
                typer.echo(
                    f"{'settings':14} tz={settings_row.timezone} lang={settings_row.language} "
                    f"lang={settings_row.language} targets_manual={settings_row.targets_manual} "
                    f"kcal={settings_row.target_kcal}"
                )

    _run(_inner())


if __name__ == "__main__":  # pragma: no cover
    configure_logging(json_logs=False)
    cli()
