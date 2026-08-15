"""Development CLI for the ConnectLife integration.

Usage:
    uv run cli.py login                # save credentials and pre-fetch a token
    uv run cli.py devices              # pretty table per device
    uv run cli.py devices --raw        # raw JSON dump
    uv run cli.py status <puid>        # single device status table
    uv run cli.py set <puid> key=val … # push property updates

Credentials and the access token are cached in ~/.config/connectlife/cli-cache.json
so subsequent calls skip re-authentication entirely.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

# Make custom_components importable when running from this directory.
sys.path.insert(0, str(Path(__file__).parent))

import typer
from rich import print_json
from rich.console import Console
from rich.table import Table

from custom_components.connectlife.api import ConnectLifeApi
from custom_components.connectlife.const import TEMP_CODE_FAHRENHEIT

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)
console = Console()

# ---------------------------------------------------------------------------
# Credential / token cache
# ---------------------------------------------------------------------------

_CACHE_PATH = Path.home() / ".config" / "connectlife" / "cli-cache.json"


def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(data: dict) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(data, indent=2))
    _CACHE_PATH.chmod(0o600)  # user-readable only


def _inject_cached_token(api: ConnectLifeApi, cache: dict) -> None:
    """Restore a previously saved token into the API client, if still valid."""
    token = cache.get("access_token")
    valid_until = cache.get("token_valid_until", 0.0)
    if token and time.time() < valid_until:
        api.client._access_token = token
        api.client._expires = dt.datetime.fromtimestamp(valid_until)


def _persist_token(api: ConnectLifeApi, cache: dict) -> None:
    """Write the current API token back to the cache file."""
    token = api.client._access_token
    expires = api.client._expires
    if not token or expires is None:
        return
    _save_cache({**cache, "access_token": token, "token_valid_until": expires.timestamp()})


def _credentials_from_cache_or_prompt(
    username_opt: str, password_opt: str, cache: dict
) -> tuple[str, str]:
    """Return (username, password), preferring CLI args → cache → interactive prompt."""
    username = username_opt or cache.get("username") or typer.prompt("Username")
    password = (
        password_opt
        or cache.get("password")
        or typer.prompt("Password", hide_input=True)
    )
    return username, password


# ---------------------------------------------------------------------------
# API session context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _api_session(username: str, password: str):
    """Async context manager yielding a ready-to-use (api, cache) tuple.

    Injects a cached token on entry and persists any newly fetched token on exit.
    """
    cache = _load_cache()
    api = ConnectLifeApi(username, password)
    _inject_cached_token(api, cache)
    try:
        yield api, cache
    finally:
        _persist_token(api, cache)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

UsernameOpt = Annotated[
    str,
    typer.Option(
        "--username", "-u", envvar="CONNECTLIFE_LOGIN", help="Override cached username"
    ),
]
PasswordOpt = Annotated[
    str,
    typer.Option(
        "--password",
        "-p",
        envvar="CONNECTLIFE_PASSWORD",
        help="Override cached password",
        hide_input=True,
    ),
]


def _temp_unit_label(status: dict) -> str:
    return "°F" if str(status.get("t_temp_type", "0")) == TEMP_CODE_FAHRENHEIT else "°C"


def _render_device(device: dict) -> None:
    puid = device.get("puid", "?")
    nick = device.get("deviceNickName") or puid
    model = (
        f"{device.get('deviceTypeCode', '?')}-{device.get('deviceFeatureCode', '?')}"
    )
    status = device.get("statusList", {})
    unit = _temp_unit_label(status)

    table = Table(
        title=f"[bold]{nick}[/bold]  [dim]{puid}  {model}[/dim]",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Property", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")
    table.add_column("Notes", style="dim")

    _NOTES = {
        "t_power": lambda v: "on" if str(v) == "1" else "off",
        "t_work_mode": lambda v: {
            "0": "fan only",
            "1": "heat",
            "2": "cool",
            "3": "dry",
            "4": "auto",
        }.get(str(v), ""),
        "t_fan_speed": lambda v: {
            "0": "auto",
            "1": "super low",
            "2": "low",
            "3": "medium",
            "4": "high",
            "5": "super low",
            "6": "low",
            "7": "medium",
            "8": "high",
            "9": "super high",
        }.get(str(v), ""),
        "t_temp": lambda v: f"target {v}{unit}",
        "f_temp_in": lambda v: f"current {v}{unit}",
        "t_eco": lambda v: "eco on" if str(v) == "1" else "eco off",
        "t_up_down": lambda v: "swing on" if str(v) == "1" else "swing off",
        "t_fan_mute": lambda v: "mute on" if str(v) == "1" else "mute off",
        "t_temp_type": lambda v: (
            "fahrenheit" if str(v) == TEMP_CODE_FAHRENHEIT else "celsius"
        ),
        "daily_energy_kwh": lambda v: f"{v} kWh today",
    }

    for key, value in sorted(status.items()):
        note = ""
        if key in _NOTES:
            try:
                note = _NOTES[key](value)
            except Exception:
                pass
        table.add_row(key, str(value), note)

    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def login(
    username: UsernameOpt = "",
    password: PasswordOpt = "",
) -> None:
    """Save credentials and pre-fetch an access token.

    Run this once; subsequent commands will skip authentication entirely.
    """
    cache = _load_cache()
    username, password = _credentials_from_cache_or_prompt(username, password, cache)

    async def _do():
        api = ConnectLifeApi(username, password)
        with console.status("Authenticating…"):
            await api.client.login()
        _persist_token(api, {**cache, "username": username, "password": password})

    asyncio.run(_do())
    console.print(
        f"[green]Logged in as[/green] {username}. Token cached to {_CACHE_PATH}"
    )


@app.command()
def devices(
    username: UsernameOpt = "",
    password: PasswordOpt = "",
    raw: Annotated[
        bool, typer.Option("--raw", help="Dump raw JSON instead of tables")
    ] = False,
    all_devices: Annotated[
        bool, typer.Option("--all", help="Include offline / non-AC devices")
    ] = False,
) -> None:
    """List all devices and their current status."""
    cache = _load_cache()
    username, password = _credentials_from_cache_or_prompt(username, password, cache)

    async def _fetch():
        async with _api_session(username, password) as (api, _cache):
            if all_devices:
                return await api.get_devices()
            return await api.get_online_ac_devices()

    result = asyncio.run(_fetch())

    if not result:
        console.print("[yellow]No devices returned.[/yellow]")
        raise typer.Exit(1)

    if raw:
        print_json(json.dumps(result, indent=2))
        return

    console.print(f"[bold green]{len(result)} device(s) found[/bold green]\n")
    for device in result:
        _render_device(device)


@app.command()
def status(
    puid: Annotated[str, typer.Argument(help="Device puid")],
    username: UsernameOpt = "",
    password: PasswordOpt = "",
    raw: Annotated[bool, typer.Option("--raw")] = False,
) -> None:
    """Show the current status of a single device."""
    cache = _load_cache()
    username, password = _credentials_from_cache_or_prompt(username, password, cache)

    async def _fetch():
        async with _api_session(username, password) as (api, _cache):
            all_devs = await api.get_devices()
            for d in all_devs:
                if d.get("puid") == puid:
                    return d
            return None

    device = asyncio.run(_fetch())
    if device is None:
        console.print(f"[red]Device '{puid}' not found.[/red]")
        raise typer.Exit(1)

    if raw:
        print_json(json.dumps(device, indent=2))
        return

    _render_device(device)


@app.command(name="set")
def set_property(
    puid: Annotated[str, typer.Argument(help="Device puid")],
    properties: Annotated[
        list[str],
        typer.Argument(help="key=value pairs, e.g. t_power=1 t_temp=22"),
    ],
    username: UsernameOpt = "",
    password: PasswordOpt = "",
) -> None:
    """Push one or more property updates to a device.

    Example: uv run cli.py set <puid> t_power=1 t_temp=22
    """
    parsed: dict[str, int | str] = {}
    for item in properties:
        if "=" not in item:
            console.print(f"[red]Invalid property '{item}' — expected key=value[/red]")
            raise typer.Exit(1)
        key, _, value = item.partition("=")
        try:
            parsed[key.strip()] = int(value.strip())
        except ValueError:
            parsed[key.strip()] = value.strip()

    console.print(f"Sending to [cyan]{puid}[/cyan]: {parsed}")
    cache = _load_cache()
    username, password = _credentials_from_cache_or_prompt(username, password, cache)

    async def _push():
        async with _api_session(username, password) as (api, _cache):
            return await api.update_device(puid, parsed)

    result = asyncio.run(_push())
    console.print("[green]Response:[/green]")
    print_json(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
