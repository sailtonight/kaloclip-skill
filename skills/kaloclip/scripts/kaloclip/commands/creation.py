"""`clip script|video|analysis|job` — the credit-spending async jobs and their polling."""

from __future__ import annotations

from pathlib import Path

from .. import api, render
from ..core import Command, Flag
from ..errors import ClipError, UsageError
from . import common
from .assets import resolve_product

SCRIPT_MAX = 8000
DESCRIPTION_MAX = 1000
SCRIPT_CREDITS = 2  # flat per draft, refunded on failure
ANALYSIS_CREDITS = 1  # flat per analysis, refunded on failure

# Fields a remembered script draft shares with POST /videos.
_CARRY = (
    "imageInfos", "productTitle", "categoryNames", "sellingPoints", "creationDescription",
    "productId", "country", "sellingCountry", "language", "duration", "internalModelId",
)


# --- shared creation inputs ----------------------------------------------------

def product_flags() -> list[Flag]:
    return [
        Flag("--product-link", "fill title/categories/selling points/images from a product link "
             "(explicit flags still win)", metavar="URL"),
        common.image_flag(),
        Flag("--title", "product title", metavar="TEXT"),
        Flag("--category", "product category name (repeat)", kind="multi", default=[], metavar="NAME"),
        Flag("--selling-point", "selling point (repeat)", kind="multi", default=[], metavar="TEXT"),
        Flag("--description", f"creative brief / what the user wants (≤{DESCRIPTION_MAX} chars)", metavar="TEXT"),
        Flag("--product-id", "product id (filled by --product-link)", metavar="ID"),
        Flag("--country", "product market, e.g. US", metavar="CC"),
        Flag("--selling-country", "market the video sells into, e.g. US", metavar="CC"),
        Flag("--language", "script/video language code, e.g. en, zh (default en)", metavar="LANG"),
    ]


def creation_fields(cfg, opts, base: dict | None = None) -> dict:
    """Merge: base (resolved product or earlier job input) < explicit flags."""
    # local checks first: a bad flag must not cost a (rate-limited) product resolve
    images = common.images(opts.get("image"), required=False)
    duration = common.duration(opts)
    description = opts.get("description")
    if description and len(description) > DESCRIPTION_MAX:
        raise UsageError(f"--description is {len(description)} chars; the limit is {DESCRIPTION_MAX}")
    fields = {k: v for k, v in (base or {}).items() if k in _CARRY and v not in (None, "", [])}
    link = opts.get("product_link")
    if link:
        p = resolve_product(cfg, link, opts.get("country"), opts.get("language"))
        fields.update(common.compact({
            "imageInfos": [{"assetId": i.get("assetId"), "imageUrl": i.get("imageUrl")}
                           for i in p.get("imageInfos") or [] if i.get("assetId") is not None][: common.IMAGES_MAX],
            "productTitle": p.get("productTitle"),
            "categoryNames": p.get("categoryNames"),
            "sellingPoints": p.get("sellingPoints"),
            "productId": p.get("productId"),
            "country": p.get("country"),
        }))
    fields.update(common.compact({
        "imageInfos": images,
        "productTitle": opts.get("title"),
        "categoryNames": opts.get("category"),
        "sellingPoints": opts.get("selling_point"),
        "creationDescription": description,
        "productId": opts.get("product_id"),
        "country": opts.get("country"),
        "sellingCountry": opts.get("selling_country"),
        "language": opts.get("language"),
        "duration": duration,
        "internalModelId": opts.get("model"),
    }))
    return fields


def require(fields: dict, needs: dict[str, str], extra_hint: str | None = None) -> None:
    missing = [flag for key, flag in needs.items() if fields.get(key) in (None, "", [])]
    if missing:
        hints = ["--product-link fills title, categories and images; models and durations come from `clip options`"]
        raise UsageError(f"missing: {', '.join(missing)}", ([extra_hint] if extra_hint else []) + hints)


def _then_wait(cfg, opts, path: str, job_id, noun: str, next_hint: str) -> int:
    if opts.get("wait") and job_id is not None:
        if not opts.get("json"):
            render.emit_detail(noun, {"job_id": job_id})
        job = common.wait_job(cfg, path, str(job_id), int(opts.get("timeout") or common.WAIT_DEFAULT))
        return common.emit_job(job, str(job_id), opts, waited=True)
    return common.emit_submitted(noun, job_id, next_hint, opts)


# --- script ------------------------------------------------------------------

def script_create(cfg, opts, args):
    fields = creation_fields(cfg, opts)
    fields.setdefault("language", "en")
    require(fields, {
        "imageInfos": "--image (or --product-link)", "productTitle": "--title", "categoryNames": "--category",
        "duration": "--duration", "internalModelId": "--model",
    })
    body = {"creditSourceType": common.CREDIT_SOURCE, **fields}
    job_id = common.submit(cfg, opts, "/scripts", body, extra_preview={"credits": SCRIPT_CREDITS})
    if job_id is None:
        return 0
    # /scripts takes no resolution; keep the chosen one for `video create --script-job`
    common.remember_job(job_id, {**body, **common.compact({"resolution": opts.get("resolution")})})
    return _then_wait(cfg, opts, "/jobs", job_id, "script_job",
                      f"Poll for the script: `clip script get {job_id} --wait` (usually under a minute)")


def script_get(cfg, opts, args):
    job_id = common.job_id_arg(args[0])
    if opts.get("wait"):
        job = common.wait_job(cfg, "/scripts", job_id, int(opts.get("timeout") or common.WAIT_DEFAULT))
        return common.emit_job(job, job_id, opts, waited=True)
    return common.emit_job(common.get_job(cfg, "/scripts", job_id), job_id, opts)


def script_candidates(cfg, opts, args):
    job_id = common.job_id_arg(args[0])
    data = api.request(cfg, "GET", "/script-generations/resolve", params={"jobId": job_id}) or {}
    if opts.get("json"):
        render.emit_json(data)
        return 0
    render.emit_detail("rewrite_job", common.compact({
        "job_id": job_id, "state": data.get("state"), "script_type": data.get("scriptType"),
        "error": data.get("errorMessage"),
    }))
    scripts = data.get("scripts") or []
    if scripts:
        render.emit_table("scripts", [{"index": s.get("scriptIndex"), "script": s.get("script")} for s in scripts],
                          ("index", "script"))
        render.emit_help([
            "Pick one with the user, save it to a file, then: `clip video create --script-file <file> "
            "--script-type FIDELITY --title ... --image ... --model <MODEL> --duration <S>`",
        ])
    elif data.get("state") in common.OPEN_STATES:
        render.emit_help([f"Still {data.get('state')} — check again shortly: `clip script candidates {job_id}`"])
    return 0


# --- video -------------------------------------------------------------------

def _preview_body(fields: dict, opts, resolution: str | None = None) -> dict:
    return {
        "aspectRatio": common.ratio(opts),
        "duration": fields.get("duration"),
        "resolution": resolution or opts.get("resolution") or "720P",
        "internalModelId": fields.get("internalModelId"),
        "quantity": 1,
    }


def video_preview(cfg, opts, args):
    common.need(opts, "--model", "--duration")
    body = _preview_body({"duration": common.duration(opts), "internalModelId": opts["model"]}, opts)
    data = api.request(cfg, "POST", "/videos/preview", body=body)
    if opts.get("json"):
        render.emit_json(data)
        return 0
    render.emit_detail("video_preview", {
        "model": body["internalModelId"], "duration": body["duration"], "resolution": body["resolution"],
        "ratio": opts.get("ratio") or "9:16", "credits": data,
    })
    render.emit_help(["An estimate only: the submit can still be refused (queue, membership, inputs)"])
    return 0


def _script_text(cfg, opts) -> tuple[str | None, dict | None]:
    """-> (final script, inputs the --script-job draft was submitted with, if drafted here).

    --script / --script-file give the text; --script-job alone takes the draft's
    text, and with either of the others only lends its inputs (an edited draft).
    """
    if opts.get("script") and opts.get("script_file"):
        raise UsageError("use only one of --script, --script-file")
    text = opts.get("script")
    if opts.get("script_file"):
        try:
            text = Path(opts["script_file"]).expanduser().read_text(encoding="utf-8").strip()
        except OSError as e:
            raise UsageError(f"cannot read --script-file: {e.strerror or e}") from None
    if not opts.get("script_job"):
        return text, None
    job_id = common.job_id_arg(opts["script_job"])
    if text:
        return text, common.recall_job(job_id)
    job = common.get_job(cfg, "/scripts", job_id)
    output = job.get("output") if isinstance(job.get("output"), dict) else {}
    if job.get("state") != "completed" or not output.get("script"):
        raise ClipError(
            f"script job {job_id} is {job.get('state') or 'unknown'}, no script to use yet",
            kind="parameter",
            help_lines=[f"Wait for it: `clip script get {job_id} --wait`"],
        )
    return output["script"], common.recall_job(job_id)


def video_create(cfg, opts, args):
    script, snapshot = _script_text(cfg, opts)
    fields = creation_fields(cfg, opts, base=snapshot)
    fields.setdefault("language", "en")
    if not script:
        raise UsageError("missing the final script: --script, --script-file or --script-job",
                         ["Draft one first: `clip script create ...` → `clip script get <job_id> --wait`"])
    if len(script) > SCRIPT_MAX:
        raise UsageError(f"script is {len(script)} chars; the limit is {SCRIPT_MAX}")
    recalled = None
    if opts.get("script_job") and snapshot is None:
        recalled = (f"Draft {opts['script_job']} was not submitted from this machine, so its inputs are unknown "
                    "here — pass them again")
    require(fields, {
        "imageInfos": "--image (or --product-link)", "productTitle": "--title",
        "duration": "--duration", "internalModelId": "--model",
    }, recalled)
    preview = _preview_body(fields, opts, opts.get("resolution") or (snapshot or {}).get("resolution"))
    body = {
        "creditSourceType": common.CREDIT_SOURCE,
        **fields,
        "scriptType": opts.get("script_type") or "CREATIVE",
        "script": script,
        "aspectRatio": preview["aspectRatio"],
        "resolution": preview["resolution"],
        "quantity": 1,
    }
    extra = None
    if not opts.get("yes"):
        try:
            extra = {"credits": api.request(cfg, "POST", "/videos/preview", body=preview)}
        except ClipError as e:  # the estimate is a courtesy; the dry run itself must still show
            extra = {"credits": f"unknown ({e})"}
    job_id = common.submit(cfg, opts, "/videos", body, extra_preview=extra)
    if job_id is None:
        return 0
    return _then_wait(cfg, opts, "/jobs", job_id, "video_job",
                      f"Videos take ~15 minutes: `clip video get {job_id} --wait` (polls up to 20 min)")


def video_get(cfg, opts, args):
    job_id = common.job_id_arg(args[0])
    if opts.get("wait"):
        job = common.wait_job(cfg, "/videos", job_id, int(opts.get("timeout") or common.WAIT_DEFAULT))
        return common.emit_job(job, job_id, opts, waited=True)
    return common.emit_job(common.get_job(cfg, "/videos", job_id), job_id, opts)


# --- script analysis -----------------------------------------------------------

def analysis_create(cfg, opts, args):
    url = args[0].strip()
    if len(url) > 2048:
        raise UsageError("video URL is longer than 2048 characters")
    commerce = bool(opts.get("commerce"))
    country = opts.get("country")
    if commerce and not country:
        raise UsageError("--commerce needs --country (shoppable TikTok videos only), e.g. --country US")
    body = common.compact({
        "creditSourceType": common.CREDIT_SOURCE, "videoUrl": url, "country": country,
    })
    body["commerce"] = commerce
    job_id = common.submit(cfg, opts, "/script-analysis", body, extra_preview={"credits": ANALYSIS_CREDITS})
    if job_id is None:
        return 0
    return _then_wait(cfg, opts, "/script-analysis", job_id, "analysis_job",
                      f"Poll for the breakdown: `clip analysis get {job_id} --wait` (kept 30 days)")


def analysis_get(cfg, opts, args):
    job_id = common.job_id_arg(args[0])
    if opts.get("wait"):
        job = common.wait_job(cfg, "/script-analysis", job_id, int(opts.get("timeout") or common.WAIT_DEFAULT))
        return common.emit_job(job, job_id, opts, waited=True)
    return common.emit_job(common.get_job(cfg, "/script-analysis", job_id), job_id, opts)


# --- any job -------------------------------------------------------------------

def job(cfg, opts, args):
    job_id = common.job_id_arg(args[0])
    if opts.get("wait"):
        found = common.wait_job(cfg, "/jobs", job_id, int(opts.get("timeout") or common.WAIT_DEFAULT))
        return common.emit_job(found, job_id, opts, waited=True)
    return common.emit_job(common.get_job(cfg, "/jobs", job_id), job_id, opts)


def _submit_flags() -> list[Flag]:
    return [common.yes_flag(), common.key_flag(), *common.wait_flags()]


COMMANDS = [
    Command(
        path="script create",
        summary=f"Draft a video script from product info and images ({SCRIPT_CREDITS} credits; async job)",
        handler=script_create,
        flags=[*product_flags(), common.duration_flag(), common.model_flag(),
               common.resolution_flag(None, "; not sent — `video create --script-job` reuses it"), *_submit_flags()],
        examples=[
            "clip script create --product-link https://shop.tiktok.com/view/product/1732052189676081387 "
            "--model sd2 --duration 15",
            "clip script create --image 739230=https://resource.kaloclip.com/.../a.png --title 'Summer Dress' "
            "--category Apparel --model sd2 --duration 15 --description 'playful, outdoor' --yes --wait",
        ],
        writes=True,
    ),
    Command(
        path="script get",
        summary="State and text of a script draft job",
        handler=script_get,
        flags=common.wait_flags(),
        positional="job_id",
        pos_min=1,
        pos_max=1,
        examples=["clip script get 123456 --wait"],
    ),
    Command(
        path="script candidates",
        summary="The two rewrite candidates of a Kaloclip web rewrite job (scriptType FIDELITY)",
        handler=script_candidates,
        positional="job_id",
        pos_min=1,
        pos_max=1,
        examples=["clip script candidates 123456"],
    ),
    Command(
        path="video preview",
        summary="Credits a video with these settings would cost (free, no job)",
        handler=video_preview,
        flags=[common.model_flag(), common.duration_flag(), common.resolution_flag(), common.ratio_flag()],
        examples=["clip video preview --model sd2 --duration 15 --resolution 720P"],
    ),
    Command(
        path="video create",
        summary="Make a video from a final script (spends credits — see `video preview`; async job)",
        handler=video_create,
        flags=[
            Flag("--script", f"final script text (≤{SCRIPT_MAX} chars)", metavar="TEXT"),
            Flag("--script-file", "read the final script from a file", metavar="PATH"),
            Flag("--script-job", "take a completed `script create` draft (and its inputs, if drafted on this "
                 "machine); with --script/--script-file only its inputs", metavar="JOB_ID"),
            Flag("--script-type", "CREATIVE (default) | FIDELITY (rewrite candidates)", choices=common.SCRIPT_TYPES,
                 metavar="TYPE"),
            *product_flags(), common.duration_flag(), common.model_flag(),
            common.resolution_flag(None, " (default: the --script-job draft's, else 720P)"), common.ratio_flag(),
            *_submit_flags(),
        ],
        examples=[
            "clip video create --script-job 123456",
            "clip video create --script-job 123456 --script-file edited.txt --resolution 1080P",
            "clip video create --script-job 123456 --yes --idempotency-key <KEY_FROM_DRY_RUN> --wait",
        ],
        writes=True,
    ),
    Command(
        path="video get",
        summary="State of a video job; videoUrl once completed",
        handler=video_get,
        flags=common.wait_flags(),
        positional="job_id",
        pos_min=1,
        pos_max=1,
        examples=["clip video get 123457 --wait"],
    ),
    Command(
        path="analysis create",
        summary="Break an existing TikTok / Kalodata / YouTube Shorts video into shots, voice-over, "
                f"camera moves ({ANALYSIS_CREDITS} credit; async job)",
        handler=analysis_create,
        flags=[
            Flag("--commerce", "shoppable video analysis (TikTok only, needs --country)", kind="flag", default=False),
            Flag("--country", "market of the shoppable video, e.g. US", metavar="CC"),
            *_submit_flags(),
        ],
        positional="video_url",
        pos_min=1,
        pos_max=1,
        examples=["clip analysis create https://www.tiktok.com/@shop/video/7400000000000000000"],
        writes=True,
    ),
    Command(
        path="analysis get",
        summary="State and shot-by-shot result of a script analysis job (kept 30 days)",
        handler=analysis_get,
        flags=common.wait_flags(),
        positional="job_id",
        pos_min=1,
        pos_max=1,
        examples=["clip analysis get 123458 --wait"],
    ),
    Command(
        path="job",
        summary="State and output of any clip job by id",
        handler=job,
        flags=common.wait_flags(),
        positional="job_id",
        pos_min=1,
        pos_max=1,
        examples=["clip job 123456 --wait"],
    ),
]
