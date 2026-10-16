"""Free calls: creation options, credit balance, image assets, product link resolve."""

from __future__ import annotations

from pathlib import Path

from .. import api, render
from ..core import Command, Flag
from ..errors import UsageError
from . import common

IMAGE_MAX_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".gif", ".webp")


_RATIO_LABELS = {v: k for k, v in common.RATIOS.items() if ":" in k}


def options(cfg, opts, args):
    data = api.request(cfg, "GET", "/videos/options", params={"video-language": opts.get("language")}) or {}
    if opts.get("json"):
        render.emit_json(data)
        return 0
    if "models" not in data:
        return _legacy_options(data)
    rows = [
        {"model": m.get("internalModelId"), "name": m.get("name"), "duration": c.get("duration"),
         "resolution": c.get("resolution"), "credits": c.get("credits")}
        for m in data.get("models") or [] for c in m.get("combinations") or []
    ]
    if not rows:
        render.emit_empty("combinations", "no model can be submitted right now",
                          ["Try again later; with --language th some models are hidden"])
        return 0
    render.emit_table("combinations", rows, ("model", "name", "duration", "resolution", "credits"))
    render.emit_detail("other", {"ratios": [_RATIO_LABELS.get(r, r) for r in data.get("aspectRatios") or []]})
    render.emit_help([
        "Pick one row: --model <model> --duration <duration> --resolution <resolution>; only listed "
        "combinations are accepted — never use one from memory",
        "credits is the price of one video; `clip credit` must show at least that much",
        "Query with the --language the video will use (th hides some models)",
    ])
    return 0


def _legacy_options(data: dict) -> int:
    """The pre-credits shape (modelOptions + duration rules), kept while gateways roll over."""
    rules = data.get("rules") or {}
    by_duration = rules.get("duration_model") or {}
    usable = {m for ms in by_duration.values() for m in ms or []}
    # labels/descriptions are untranslated i18n keys upstream; the ids are what matters
    models = [
        {"model": m.get("value"),
         "durations": "|".join(d for d, ms in by_duration.items() if m.get("value") in (ms or [])) or None}
        for m in data.get("modelOptions") or []
    ]
    render.emit_table("models", models, ("model", "durations"))
    durations = [
        {
            "seconds": d.get("value"),
            "default": bool(d.get("default")),
            "models": "|".join(by_duration.get(str(d.get("value"))) or []) or None,
        }
        for d in data.get("durationOptions") or []
    ]
    render.emit_table("durations", durations, ("seconds", "default", "models"))
    render.emit_detail("other", {
        "resolutions": [r.get("value") for r in data.get("resolutionOptions") or []],
        "ratios": [r.get("label") for r in data.get("aspectRatioOptions") or []],
    })
    unusable = [m["model"] for m in models if m["model"] not in usable]
    if unusable:
        render.emit_help([
            f"{', '.join(unusable)} not in the duration rules — the rules can be incomplete, so check a "
            "combination with `clip video preview --model <MODEL> --duration <S>` (free) before using it",
        ])
    render.emit_help([
        "A --model only works with the durations listed for it; pick both from this output, never from memory",
        "Estimate credits before submitting: `clip video preview --model <MODEL> --duration <S> --resolution 720P`",
    ])
    return 0


def credit(cfg, opts, args):
    data = api.request(cfg, "GET", "/credits") or {}
    if opts.get("json"):
        render.emit_json(data)
        return 0
    display = common.compact({
        "total_remain": data.get("totalRemain"),
        "monthly_remain": data.get("monthlyRemain"),
        "permanent_remain": data.get("permanentRemain"),
    })
    if data.get("subAccount"):
        display["main_total_remain"] = data.get("mainTotalRemain")
    display["account"] = "sub-account" if data.get("subAccount") else "main account"
    render.emit_detail("credit_balance", display)
    render.emit_help(["The same credits back `kalo` (KaloData) queries; Kaloclip prices apply to clip jobs"])
    return 0


def _asset_rows(data) -> list[dict]:
    rows = data if isinstance(data, list) else []
    return [
        {"asset_id": r.get("assetId"), "ok": bool(r.get("success", r.get("assetId") is not None)),
         "image_url": r.get("imageUrl") or None}
        for r in rows if isinstance(r, dict)
    ]


def _emit_assets(data, opts) -> int:
    if opts.get("json"):
        render.emit_json(data)
        return 0
    rows = _asset_rows(data)
    render.emit_table("images", rows, ("asset_id", "ok", "image_url"))
    good = [r for r in rows if r["asset_id"] is not None]
    hints = []
    if good:
        flags = " ".join(f"--image {r['asset_id']}={r['image_url']}" for r in good)
        hints.append(f"Use them in `clip script create` / `clip video create`: {flags}")
    if len(good) < len(rows):
        hints.append("Rows with ok=false were not stored (unsupported, too large or unreachable) — retry those alone")
    render.emit_help(hints)
    return 0


def upload(cfg, opts, args):
    if len(args) > common.IMAGES_MAX:
        raise UsageError(f"at most {common.IMAGES_MAX} files per call, got {len(args)}")
    paths = []
    for a in args:
        p = Path(a).expanduser()
        if not p.is_file():
            raise UsageError(f"no such file: {a}")
        if p.suffix.lower() not in IMAGE_TYPES:
            raise UsageError(f"{a}: only JPEG/PNG/GIF/WebP images are accepted")
        if p.stat().st_size > IMAGE_MAX_BYTES:
            raise UsageError(f"{a} is {p.stat().st_size // 1024} KB; the limit is 5 MB per image")
        paths.append(p)
    data = api.request(cfg, "POST", "/images", files=("images", paths))
    return _emit_assets(data, opts)


def import_(cfg, opts, args):
    if len(args) > common.IMAGES_MAX:
        raise UsageError(f"at most {common.IMAGES_MAX} URLs per call, got {len(args)}")
    bad = [a for a in args if not a.startswith("https://")]
    if bad:
        raise UsageError(f"public https:// image URLs only, got {bad[0]}")
    data = api.request(cfg, "POST", "/images/import", body=list(args))
    return _emit_assets(data, opts)


def resolve_product(cfg, link: str, country: str | None, language: str | None) -> dict:
    if len(link) > 2048:
        raise UsageError("product link is longer than 2048 characters")
    body = common.compact({"productLink": link, "region": country, "language": language})
    return api.request(cfg, "POST", "/products/resolve", body=body) or {}


def product(cfg, opts, args):
    data = resolve_product(cfg, args[0], opts.get("country"), opts.get("language"))
    if opts.get("json"):
        render.emit_json(data)
        return 0
    imgs = data.get("imageInfos") or []
    render.emit_detail("product", common.compact({
        "product_id": data.get("productId"),
        "title": data.get("productTitle"),
        "country": data.get("country"),
        "language": data.get("language"),
        "categories": data.get("categoryNames"),
        "selling_points": data.get("sellingPoints"),
    }))
    render.emit_table("images", [
        {"asset_id": i.get("assetId"), "description": i.get("description"), "image_url": i.get("imageUrl")}
        for i in imgs
    ], ("asset_id", "description", "image_url"))
    hints = [f"Draft a script straight from the link: `clip script create --product-link '{args[0]}' "
             "--model <MODEL> --duration <S>` (models: `clip options`)"]
    if not data.get("sellingPoints"):
        hints.append("selling_points is empty while Kaloclip is still analysing — rerun in a minute, or go on without")
    render.emit_help(hints)
    return 0


COMMANDS = [
    Command(
        path="options",
        summary="Model × duration × resolution combinations accepted right now, with credits per video (free)",
        handler=options,
        flags=[Flag("--language", "language the video will use, e.g. en; th hides some models", metavar="LANG")],
        examples=["clip options", "clip options --language en"],
    ),
    Command(
        path="credit",
        summary="Remaining credits of the account this key belongs to (free)",
        handler=credit,
        examples=["clip credit"],
    ),
    Command(
        path="image upload",
        summary=f"Upload 1-{common.IMAGES_MAX} local images (JPEG/PNG/GIF/WebP, ≤5MB) as creation assets (free)",
        handler=upload,
        positional="file",
        pos_min=1,
        pos_max=None,
        examples=["clip image upload front.png side.jpg"],
    ),
    Command(
        path="image import",
        summary=f"Import 1-{common.IMAGES_MAX} public https image URLs as creation assets (free)",
        handler=import_,
        positional="url",
        pos_min=1,
        pos_max=None,
        examples=["clip image import https://example.com/product.jpg"],
    ),
    Command(
        path="product resolve",
        summary="TikTok Shop / Kalodata / Amazon product link → title, categories, selling points, image assets (free)",
        handler=product,
        flags=[
            Flag("--country", "override the link's market, e.g. US", metavar="CC"),
            Flag("--language", "language of the returned text, e.g. en, zh", metavar="LANG"),
        ],
        positional="link",
        pos_min=1,
        pos_max=1,
        examples=["clip product resolve https://shop.tiktok.com/view/product/1732052189676081387?region=US"],
    ),
]
