"""`clip config` — show/save credentials and the endpoint."""

from __future__ import annotations

from .. import config as config_mod
from .. import onboarding, render
from ..core import Command, Flag
from ..errors import UsageError

# `--base-url staging` / `prod` are shorthands for the two published gateways
_ENDPOINTS = {"prod": config_mod.DEFAULT_BASE_URL, "production": config_mod.DEFAULT_BASE_URL,
              "staging": config_mod.STAGING_BASE_URL}


def config_show(cfg, opts, args):
    path = config_mod.config_path()
    masked = "missing"
    if cfg.api_key:
        masked = f"set (…{cfg.api_key[-4:]})" if len(cfg.api_key) > 4 else "set"
    data = {
        "file": str(path) + ("" if path.exists() else " (missing)"),
        "api_key": masked + " — shared with `kalo`",
        "base_url": cfg.base_url,
        "update_check": "on" if cfg.update_check else "off",
    }
    if opts.get("json"):
        render.emit_json(data)
        return 0
    render.emit_detail("config", data)
    if not cfg.authed:
        render.emit_help(onboarding.SETUP_LINES)
    return 0


def config_set(cfg, opts, args):
    base = opts.get("base_url")
    if base:
        base = _ENDPOINTS.get(base.lower(), base)
        if not base.startswith(("https://", "http://")):
            raise UsageError(f"--base-url must be a URL, prod or staging, got '{opts['base_url']}'")
    updates = {
        "api_key": opts.get("key"),
        "clip_base_url": base,
        "clip_update_check": {"on": "true", "off": "false"}.get(opts.get("update_check") or ""),
    }
    updates = {k: v for k, v in updates.items() if v is not None}
    if not updates:
        raise UsageError("nothing to set", ["usage: clip config set --key <API_KEY> [--base-url staging]"])
    path = config_mod.save(updates)
    render.out(f"config: saved {', '.join(sorted(updates))} to {path}")
    return 0


COMMANDS = [
    Command(
        path="config show",
        summary="Show current credentials (masked) and endpoint",
        handler=config_show,
        examples=["clip config"],
    ),
    Command(
        path="config set",
        summary="Save credentials/endpoint to ~/.config/kalodata/config.toml (shared with kalo)",
        handler=config_set,
        flags=[
            Flag("--key", "KaloData OpenAPI key (secret-key header; also used by kalo)", metavar="KEY"),
            Flag("--base-url", "gateway: prod (default) | staging | a full URL", metavar="URL"),
            Flag("--update-check", "upgrade notice from GitHub tags (default on)", choices=("on", "off"),
                 metavar="on|off"),
        ],
        examples=["clip config set --key <token>", "clip config set --base-url staging"],
    ),
]
