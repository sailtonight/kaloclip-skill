"""Upgrade notice: compare __version__ against the newest tag on GitHub.

Never blocks a command. The foreground run only reads the cache written by a
previous run and, when that cache is stale, detaches a child process to refresh
it. Two independent 1h debounces live in the cache: `checked_at` throttles the
GitHub request (bumped even when it fails, so a broken network does not retry on
every invocation) and `notified_at` throttles the banner.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import __version__
from .config import Config, config_path

REPO = "sailtonight/kaloclip-skill"
TAGS_URL = f"https://api.github.com/repos/{REPO}/tags?per_page=20"
UPGRADE_CMD = f"npx skills add {REPO}"
DEFAULT_INTERVAL = 3600.0
_TAG_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")
_ENV_DISABLE = "KALOCLIP_NO_UPDATE_CHECK"
_ENV_INTERVAL = "KALOCLIP_UPDATE_INTERVAL"


def state_path() -> Path:
    return config_path().parent / "update-check-clip.json"


def parse_version(tag: str) -> tuple[int, ...] | None:
    """`v1.2.3` -> (1, 2, 3). Prerelease and non-numeric tags are ignored."""
    m = _TAG_RE.match((tag or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _read_state() -> dict:
    try:
        with open(state_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(updates: dict) -> None:
    """Read-modify-write, atomically — a refresh child and the parent can race."""
    data = _read_state()
    data.update(updates)
    path = state_path()
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _urlopen_json(url: str, timeout: float):
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"clip/{__version__}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_latest_tag(timeout: float = 5.0) -> str | None:
    """Newest numeric tag on GitHub, or None if unreachable/private/untagged."""
    tags = _urlopen_json(TAGS_URL, timeout)
    if not isinstance(tags, list):
        return None
    best, best_key = None, None
    for tag in tags:
        name = tag.get("name") if isinstance(tag, dict) else None
        key = parse_version(name) if name else None
        if key and (best_key is None or key > best_key):
            best, best_key = name, key
    return best


def refresh() -> None:
    """Child-process entry: hit GitHub and cache the result. Failures stay silent."""
    updates = {"checked_at": time.time()}
    try:
        latest = fetch_latest_tag()
        if latest:
            updates["latest"] = latest
    except Exception:  # noqa: BLE001,S110 - an upgrade check must fail silently
        pass
    _write_state(updates)


def _spawn_refresh() -> None:
    scripts_dir = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [scripts_dir, env.get("PYTHONPATH")]))
    env[_ENV_DISABLE] = "1"  # the child must not recurse
    subprocess.Popen(
        [sys.executable, "-m", "kaloclip.update"],
        cwd=scripts_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _interval() -> float:
    try:
        return max(0.0, float(os.environ.get(_ENV_INTERVAL, DEFAULT_INTERVAL)))
    except ValueError:
        return DEFAULT_INTERVAL


def _enabled(cfg: Config) -> bool:
    if os.environ.get(_ENV_DISABLE, "").strip().lower() not in ("", "0", "false", "no"):
        return False
    return cfg.update_check


def notice_line(latest: str) -> str:
    return f"update[1]: clip {__version__} → {latest} available · {UPGRADE_CMD}"


def check(cfg: Config, stream=None) -> str | None:
    """Print an upgrade notice to stderr if one is due; kick off a refresh if stale.

    Wrapped in a blanket except by the caller's contract: an upgrade hint must
    never turn a working query into a failure.
    """
    try:
        if not _enabled(cfg):
            return None
        state = _read_state()
        now, interval = time.time(), _interval()

        if now - float(state.get("checked_at") or 0) >= interval:
            # claim the slot before spawning so concurrent runs fire one child
            _write_state({"checked_at": now})
            _spawn_refresh()

        latest = state.get("latest")
        key = parse_version(latest) if isinstance(latest, str) else None
        if not key or key <= parse_version(__version__):
            return None
        if now - float(state.get("notified_at") or 0) < interval:
            return None
        _write_state({"notified_at": now})
        line = notice_line(latest)
        print(line, file=stream or sys.stderr)
        return line
    except Exception:  # noqa: BLE001 - never let an upgrade hint break a query
        return None


if __name__ == "__main__":
    refresh()
