"""Content for the no-args home view.

The SKILL.md next to this package mirrors this command table — keep both
in sync when commands change.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from . import __version__, onboarding, toon
from .config import Config

DESCRIPTION = (
    "AI short-video creation with Kaloclip — product link or images → script draft → "
    "finished TikTok-style video; script analysis of existing videos"
)

# (command, what) — the `what` column is deliberately terse; details live in --help
COMMAND_TABLE = [
    ("clip options", "model × duration × resolution rows with credits (free)"),
    ("clip credit", "remaining credits (free)"),
    ("clip product resolve <link>", "product link → title, categories, selling points, image assets (free)"),
    ("clip asset list", "assets already in the account, e.g. past videos: --type VID --source AI (free)"),
    ("clip image upload <file...>", "local images → asset ids, 1-6, ≤5MB each (free)"),
    ("clip image import <url...>", "public image URLs → asset ids (free)"),
    ("clip script create", "[credits] draft a script; --product-link or --image/--title/--category"),
    ("clip script get <job_id>", "script draft state and text; --wait"),
    ("clip script candidates <job_id>", "the 2 candidates of a Kaloclip web rewrite job"),
    ("clip video preview", "credits a video would cost (free)"),
    ("clip video create", "[credits] final script → video; --script-job reuses a draft's inputs"),
    ("clip video get <job_id>", "video state; videoUrl when done; --wait"),
    ("clip analysis create <video_url>", "[credits] shot-by-shot breakdown of an existing video"),
    ("clip analysis get <job_id>", "analysis state and result; --wait"),
    ("clip job <job_id>", "any job's state and output; --wait"),
    ("clip config", "show/set key (shared with kalo) and endpoint"),
]

CONVENTIONS = [
    "[credits] commands only print a dry run until rerun with --yes — show it to the user first",
    "Video scripts come from `clip script create` (Kaloclip's model, built on top-performing ads) — don't write your own unless asked",
    "Flow: options → product resolve → script create → script get --wait → video preview → video create → video get --wait",
    "Jobs are async: keep the job id and poll (`--wait`); never resubmit a queued/processing job",
    "--image takes ASSET_ID=IMAGE_URL (repeat, 1-6); --model/--duration/--resolution from one `clip options` row",
    "Run `clip <command> --help` for per-command flags and examples",
]


def bin_path() -> str:
    found = shutil.which("clip")
    path = found or sys.argv[0] or "clip"
    home = str(Path.home())
    if path.startswith(home):
        path = "~" + path[len(home):]
    return path


def auth_line(cfg: Config) -> str:
    if cfg.authed:
        return f"auth: ok (key …{cfg.api_key[-4:]})" if len(cfg.api_key) > 4 else "auth: ok"
    return "auth: not configured — no command will run until a key is set (see setup below)"


def home_text(cfg: Config) -> str:
    lines = [
        f"bin: {bin_path()} (clip {__version__})",
        f"description: {DESCRIPTION}",
        auth_line(cfg),
        f"endpoint: {cfg.base_url}",
    ]
    if not cfg.authed:
        lines.append("")
        lines.append(f"setup[{len(onboarding.SETUP_LINES)}]:")
        lines.extend(f"  {ln}" for ln in onboarding.SETUP_LINES)
    lines += [
        "",
        toon.tabular("commands", [{"command": c, "what": w} for c, w in COMMAND_TABLE], ["command", "what"]),
        "",
        f"help[{len(CONVENTIONS)}]:",
    ]
    lines.extend(f"  {c}" for c in CONVENTIONS)
    return "\n".join(lines)
