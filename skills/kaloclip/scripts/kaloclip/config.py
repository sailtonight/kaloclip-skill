"""Configuration: environment variables first, then the shared KaloData config file.

The API key is the KaloData OpenAPI key — the same one the `kalo` CLI uses — so
it lives in ~/.config/kalodata/config.toml under `api_key`. Clip-only settings
sit in that file under a `clip_` prefix, which `kalo` ignores.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import tomllib

DEFAULT_BASE_URL = "https://www.kalodata.com/openapi/v1/clip"
STAGING_BASE_URL = "https://staging.kalodata.com/openapi/v1/clip"

# field -> (env var, key in config.toml)
SOURCES = {
    "api_key": ("KALODATA_API_KEY", "api_key"),
    "base_url": ("KALOCLIP_BASE_URL", "clip_base_url"),
    "update_check": ("KALOCLIP_UPDATE_CHECK", "clip_update_check"),
}

_FALSEY = {"0", "false", "no", "off"}


def config_path() -> Path:
    base = os.environ.get("KALODATA_CONFIG_DIR")
    root = Path(base) if base else Path.home() / ".config" / "kalodata"
    return root / "config.toml"


@dataclass
class Config:
    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    update_check: bool = True

    @property
    def authed(self) -> bool:
        return bool(self.api_key)


def _as_bool(value: str | None, fallback: bool) -> bool:
    if value is None:
        return fallback
    return value.strip().lower() not in _FALSEY


def _read_file() -> dict:
    try:
        with open(config_path(), "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def load() -> Config:
    data = _read_file()

    def pick(field: str, fallback: str | None = None) -> str | None:
        env, key = SOURCES[field]
        if os.environ.get(env):
            return os.environ[env]
        val = data.get(key)
        if val is not None and str(val) != "":
            return str(val)
        return fallback

    return Config(
        api_key=pick("api_key"),
        base_url=pick("base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL,
        update_check=_as_bool(pick("update_check"), True),
    )


def save(updates: dict) -> Path:
    """Merge `updates` (config.toml keys) into the shared file, keeping kalo's keys intact."""
    data = _read_file()
    data.update({k: v for k, v in updates.items() if v is not None})
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for k, v in data.items():
        escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{k} = "{escaped}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path
