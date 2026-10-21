"""Shared flags and helpers: image arguments, credit-spending submits, job polling, output."""

from __future__ import annotations

import datetime
import json
import os
import time
import uuid

from .. import api, config, render
from ..core import Flag
from ..errors import ClipError, UsageError

IMAGES_MAX = 6
RESOLUTIONS = ("480P", "720P", "1080P", "4K")
RATIOS = {"9:16": "RATIO_9_16", "16:9": "RATIO_16_9", "RATIO_9_16": "RATIO_9_16", "RATIO_16_9": "RATIO_16_9"}
SCRIPT_TYPES = ("CREATIVE", "FIDELITY")
CREDIT_SOURCE = "OPENAPI"  # Kaloclip's credit source for KaloData gateway callers
OPEN_STATES = ("queued", "processing")
POLL_SECONDS = 5.0
WAIT_DEFAULT = 1200  # a 15s sd2 video took ~15 min on staging; drafts ~35s
WAIT_MAX = 1800


# --- flags -------------------------------------------------------------------

def image_flag() -> Flag:
    return Flag(
        "--image",
        f"image asset as ASSET_ID=IMAGE_URL (repeat, 1-{IMAGES_MAX}); from `clip image upload|import` "
        "or `clip product resolve`",
        kind="multi",
        default=[],
        metavar="ASSET_ID=URL",
    )


def key_flag() -> Flag:
    return Flag(
        "--idempotency-key",
        "replay an earlier submit with its key so it runs at most once (default: a new key)",
        metavar="KEY",
    )


def yes_flag() -> Flag:
    return Flag(
        "--yes",
        "really submit and spend credits; without --yes the request is only printed, nothing is sent",
        kind="flag",
        default=False,
    )


def wait_flags() -> list[Flag]:
    return [
        Flag("--wait", f"poll every {int(POLL_SECONDS)}s until the job finishes", kind="flag", default=False),
        Flag("--timeout", f"max seconds to --wait (max {WAIT_MAX})", kind="int", default=WAIT_DEFAULT,
             min=1, max=WAIT_MAX, metavar="SECONDS"),
    ]


def model_flag(required_hint: str = "") -> Flag:
    return Flag("--model", "internalModelId from `clip options` (never hard-code it)" + required_hint, metavar="MODEL")


def duration_flag() -> Flag:
    return Flag("--duration", "seconds, from the same `clip options` row as --model", kind="int", metavar="SECONDS")


def resolution_flag(default: str | None = "720P", note: str = "") -> Flag:
    return Flag("--resolution", "|".join(RESOLUTIONS) + ", from the same `clip options` row as --model" + note,
                default=default, choices=RESOLUTIONS, metavar="RES")


def ratio_flag() -> Flag:
    return Flag("--ratio", "aspect ratio 9:16 | 16:9", default="9:16", choices=tuple(RATIOS), metavar="RATIO")


# --- argument helpers --------------------------------------------------------

def images(values: list[str], *, required: bool = True) -> list[dict]:
    out = []
    for v in values or []:
        asset, sep, url = v.partition("=")
        asset = asset.strip()
        url = url.strip()
        if not sep or not asset.isdigit() or not url.startswith(("http://", "https://")):
            raise UsageError(
                f"--image expects ASSET_ID=IMAGE_URL, got '{v}'",
                ["e.g. --image 739230=https://resource.kaloclip.com/resources/.../a.png",
                 "Get asset ids from `clip image upload <file>`, `clip image import <url>` or `clip product resolve <link>`"],
            )
        out.append({"assetId": int(asset), "imageUrl": url})
    if required and not out:
        raise UsageError("at least one --image is required",
                         ["Upload with `clip image upload <file>`, or use --product-link to take the product's images"])
    if len(out) > IMAGES_MAX:
        raise UsageError(f"at most {IMAGES_MAX} images per job, got {len(out)}")
    return out


def need(opts, *names: str) -> None:
    missing = [n for n in names if opts.get(n.lstrip("-").replace("-", "_")) in (None, "", [])]
    if missing:
        raise UsageError(f"missing required flag(s): {', '.join(missing)}", ["Run with --help for the full flag list"])


def duration(opts) -> int | None:
    value = opts.get("duration")
    if value is not None and value < 1:
        raise UsageError(f"--duration must be a positive number of seconds, got {value}",
                         ["Pick it from a `clip options` row"])
    return value


def ratio(opts) -> str:
    return RATIOS[opts.get("ratio") or "9:16"]


def compact(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [])}


# --- submits -----------------------------------------------------------------

def submit(cfg, opts, path: str, body: dict, *, extra_preview: dict | None = None):
    """POST a credit-spending job. Without --yes print the request instead (returns None).

    The Idempotency-Key is reused across the client's automatic retries and
    surfaced when the outcome is unknown, so the caller can replay the same
    submit without being billed twice.
    """
    key = opts.get("idempotency_key") or f"clip-{uuid.uuid4().hex}"
    # no dry run the user could approve but that could never be submitted
    api.require_auth(cfg)
    if not opts.get("yes"):
        preview = {"method": "POST", "path": path, "idempotency_key": key, **(extra_preview or {}), "body": body}
        if opts.get("json"):
            render.emit_json({"dry_run": preview})
        else:
            script = body.get("script")
            if isinstance(script, str):
                preview["body"] = {k: v for k, v in body.items() if k != "script"}
            render.emit_detail("dry_run", preview)
            if isinstance(script, str):
                emit_text("script", script)
            render.emit_help([
                "Nothing was sent and no credits were spent. Show this to the user; once they confirm, "
                f"rerun the same command with --yes --idempotency-key {key}",
            ])
        return None
    try:
        return api.request(cfg, "POST", path, body=body, headers={"Idempotency-Key": key})
    except ClipError as e:
        if e.kind == "network" or "SUBMISSION_IN_PROGRESS" in str(e):
            e.help_lines.append(
                f"The outcome is unknown — rerun the identical command with --idempotency-key {key} "
                "(within 5 minutes) so it is accepted at most once"
            )
        raise


# --- local job inputs ----------------------------------------------------------
# A job's server-side `input` snapshot only echoes creditSourceType, so the
# inputs of submitted script drafts are kept here for `video create --script-job`.

JOBS_KEPT = 50


def jobs_path():
    return config.config_path().parent / "clip-jobs.json"


def _read_jobs() -> dict:
    try:
        data = json.loads(jobs_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def remember_job(job_id, body: dict) -> None:
    """Best effort: losing this only means `--script-job` needs explicit flags."""
    jobs = _read_jobs()
    jobs[str(job_id)] = {k: v for k, v in body.items() if k != "creditSourceType"}
    jobs = dict(list(jobs.items())[-JOBS_KEPT:])
    path = jobs_path()
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def recall_job(job_id) -> dict | None:
    return _read_jobs().get(str(job_id))


# --- jobs --------------------------------------------------------------------

def get_job(cfg, path: str, job_id: str) -> dict:
    return api.request(cfg, "GET", path, params={"jobId": job_id}) or {}


def wait_job(cfg, path: str, job_id: str, timeout: int, *, sleep=time.sleep, clock=time.monotonic) -> dict:
    deadline = clock() + timeout
    while True:
        job = get_job(cfg, path, job_id)
        state = job.get("state")
        if state not in OPEN_STATES or clock() + POLL_SECONDS > deadline:
            return job
        sleep(POLL_SECONDS)


def job_id_arg(value: str) -> str:
    v = value.strip()
    if not v.isdigit():
        raise UsageError(f"job ids are numeric, got '{value}'")
    return v


def ms_iso(ms) -> str | None:
    try:
        dt = datetime.datetime.fromtimestamp(int(ms) / 1000, tz=datetime.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def job_display(job: dict) -> dict:
    out = compact({
        "job_id": job.get("id"),
        "type": job.get("jobType"),
        "state": job.get("state"),
        "error": job.get("errorMessage"),
        "error_code": job.get("errorCode"),
        **video_shape(job),
        "created": ms_iso(job.get("gmtCreated")),
        "updated": ms_iso(job.get("gmtUpdated")),
    })
    output = job.get("output")
    if isinstance(output, dict) and output:
        output = dict(output)
        # enhanceVideoUrl only stands in for videoUrl on old jobs
        if output.get("videoUrl"):
            output.pop("enhanceVideoUrl", None)
        elif output.get("enhanceVideoUrl"):
            output["videoUrl"] = output.pop("enhanceVideoUrl")
        if job.get("jobType") == "SCRIPT_ANALYSIS" and isinstance(output.get("result"), dict):
            output["result"] = analysis_display(output["result"])
        out["output"] = output
    return out


def _secs(v) -> str:
    return "?" if v is None else f"{v:g}" if isinstance(v, (int, float)) else str(v)


def analysis_display(result: dict) -> dict:
    """Readable shot list: translate_* is always null upstream and the voice-over is nested per scene."""
    out = {k: v for k, v in result.items()
           if not k.startswith("translate_") and k not in ("video_scripts", "key_to_success_list")}
    scenes = []
    for sc in result.get("video_scripts") or []:
        if not isinstance(sc, dict):
            continue
        lines = [a for a in sc.get("audio_script") or [] if isinstance(a, dict)]
        scenes.append({
            "time": f"{_secs(sc.get('start_time'))}-{_secs(sc.get('end_time'))}s",
            "scene": sc.get("scene"),
            "shot": sc.get("shot_scale"),
            "visual": sc.get("visual_description"),
            "voice_over": " ".join(f"[{_secs(a.get('start_time'))}-{_secs(a.get('end_time'))}s] {a.get('script')}"
                                   for a in lines) or None,
        })
    if scenes:
        out["scenes"] = scenes
    return out


# Failed jobs: branch on errorCode, never on errorMessage text.
_RETRY = "submit it again as a new job (new --idempotency-key)"
_FAILURE_HINTS = {
    "COPYRIGHT": "Suspected copyrighted material — replace the images/script/video involved, then " + _RETRY,
    "IMAGE_MINOR": "An image shows a minor — replace that image, then " + _RETRY,
    "IMAGE_SENSITIVE": "An image was flagged — replace it, then " + _RETRY,
    "IMAGE_REAL_PERSON": "An image looks like a real person — replace it, then " + _RETRY,
    "TEXT_SENSITIVE": "The text breaks platform rules — reword the script/description, then " + _RETRY,
    "VIDEO_SENSITIVE": "The input video was flagged — use another video, then " + _RETRY,
    "VIDEO_REAL_PERSON": "The input video shows a real person — use another video, then " + _RETRY,
    "AUDIO_SENSITIVE": "The audio was flagged — change the voice-over text, then " + _RETRY,
    "OUTPUT_SENSITIVE": "The generated result was flagged — adjust the script/description, then " + _RETRY,
    "CONTENT_SENSITIVE": "The script or video content was flagged — adjust it, then " + _RETRY,
    "SECURITY_REVIEW": "Failed the safety review — adjust the content, then " + _RETRY,
    "SCRIPT_TOO_LONG": "The script is too long for this duration — shorten it, then " + _RETRY,
}


def failure_hint(job: dict) -> str:
    """Unknown or missing codes mean a transient server failure (SERVER_BUSY)."""
    return _FAILURE_HINTS.get(job.get("errorCode") or "",
                              "Kaloclip failed on its side — wait a minute, then " + _RETRY + " with the same inputs")


_RATIO_NAMES = {"RATIO_9_16": "9:16", "RATIO_16_9": "16:9"}


def video_shape(job: dict) -> dict:
    """Duration and aspect ratio of a video job, from its input snapshot (output carries neither)."""
    snap = job.get("input") if isinstance(job.get("input"), dict) else {}
    if not str(job.get("jobType") or "").startswith("VIDEO_") or not snap:
        return {}
    duration = snap.get("duration")
    return compact({"duration": f"{duration}s" if duration else None,
                    "ratio": _RATIO_NAMES.get(snap.get("aspectRatio"), snap.get("aspectRatio"))})


def job_hints(job: dict, job_id: str, waited: bool) -> list[str]:
    state = job.get("state")
    jtype = job.get("jobType") or ""
    if state in OPEN_STATES:
        if waited:
            return [f"Still {state} after --timeout — check again later: `clip job {job_id} --wait`"]
        return [f"Still {state} — poll with `clip job {job_id} --wait` (checks every {int(POLL_SECONDS)}s); "
                "don't submit it again"]
    if state == "failed":
        return [failure_hint(job), "The credits of a failed job are refunded automatically"]
    if state != "completed":
        return []
    if jtype == "VIDEO_CREATION":
        shape = video_shape(job)
        specs = ", ".join(v for v in (shape.get("duration"), shape.get("ratio")) if v)
        return [
            "videoUrl is the finished video (coverImageUrl its cover) — hand both to the user",
            "In a chat, deliver it as a video message, not a bare link: upload the mp4 from videoUrl with "
            "coverImageUrl as its cover" + (f" and its duration and ratio ({specs})" if specs else "")
            + " — without cover and duration it shows as a blank 00:00 tile",
        ]
    if jtype == "SCRIPT_ANALYSIS":
        return ["result.scenes is the shot-by-shot breakdown (time, shot, visual, voice-over) — --json gives the raw video_scripts[]"]
    if "SCRIPT" in jtype:
        return [
            "Show the script whole — it is Kaloclip's draft, don't rewrite it or rate it against your own; "
            "to change it as the user asks, save the edited text to a file for --script-file",
            f"Make the video: `clip video create --script-job {job_id} [--script-file edited.txt]` — reuses the "
            "draft's images/title/model/duration/resolution if it was drafted on this machine, else add --product-link or "
            "--title/--image/--model/--duration; the dry run shows the credits",
        ]
    return []


def emit_job(job: dict, job_id: str, opts, *, waited: bool = False) -> int:
    if opts.get("json"):
        render.emit_json(job)
        return 0
    display = job_display(job)
    output = dict(display.get("output") or {})
    script = output.pop("script", None)
    if "output" in display:
        display["output"] = output
        if not output:
            del display["output"]
    render.emit_detail("job", display)
    if isinstance(script, str):
        emit_text("script", script)
    render.emit_help(job_hints(job, job_id, waited))
    return 0


def emit_text(name: str, text: str) -> None:
    """Long prose (scripts) verbatim, indented, instead of one escaped TOON string."""
    render.out(f"{name}[{len(text)} chars]: |")
    for line in text.splitlines():
        render.out(f"  {line}".rstrip())


def emit_submitted(noun: str, job_id, next_hint: str, opts) -> int:
    if opts.get("json"):
        render.emit_json({"jobId": job_id})
        return 0
    render.emit_detail(noun, {"job_id": job_id})
    render.emit_help([next_hint, "Keep the job id: poll it, never resubmit while it is queued/processing"])
    return 0
