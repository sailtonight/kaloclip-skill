"""HTTP client for the Kaloclip OpenAPI: retries, envelope handling, error translation.

Requests go through KaloData's gateway (data-service, `/openapi/v1/clip/**`),
which rejects a bad key or path itself with a flat `{"code", "message",
"request_id"}` body and the HTTP status that matters. Everything past the gateway
is Kaloclip's own `{"success", "code", "message", "data"}` envelope, passed
through byte for byte — there `success` decides, not the HTTP status (rate limits
and a full video queue are HTTP 200 + success=false). Never leaks raw upstream
errors to stdout — everything becomes a ClipError with an actionable suggestion.

The user is the owner of the API key. `X-User-Id` exists on the gateway for
trusted internal callers only and is never sent from here.
"""

from __future__ import annotations

import json
import mimetypes
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import __version__
from .config import Config
from .errors import ClipError
from .onboarding import NO_KEY_MESSAGE, SETUP_LINES

_RETRIES = 3

AUTH_HELP = SETUP_LINES


class _Retryable(Exception):
    pass


def require_auth(cfg: Config) -> None:
    if not cfg.api_key:
        raise ClipError(NO_KEY_MESSAGE, kind="auth", help_lines=AUTH_HELP)


def _headers(cfg: Config) -> dict:
    return {
        "User-Agent": f"clip/{__version__}",
        "Accept": "application/json",
        "secret-key": cfg.api_key,
        "source-type": "SKILL",
        "skill-version": __version__,
    }


def _do_request(url: str, data: bytes | None, headers: dict, timeout: float, method: str):
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise _Retryable(str(e)) from e


def _parse(raw: bytes):
    try:
        body = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def multipart(field: str, paths: list[Path]) -> tuple[bytes, str]:
    """Encode files as multipart/form-data under one repeated field name."""
    boundary = f"----clip{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for p in paths:
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        name = p.name.replace('"', "")
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode("utf-8")
        )
        parts.append(p.read_bytes())
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _field_errors(data) -> list[str]:
    """VALIDATION_FAILED carries {field: reason} in `data`."""
    if isinstance(data, dict):
        return [f"{k}: {v}" for k, v in data.items()]
    return []


_GATEWAY_HINTS = {
    "INSUFFICIENT_RIGHTS": "This key is not enabled for Kaloclip yet — ask KaloData to enable Kaloclip "
    "OpenAPI access for it; a retry or a new key from the same account won't help",
    "NOT_FOUND": "The gateway does not forward this path/method — the CLI and the gateway are out of "
    "sync; update the skill (`npx skills add sailtonight/kaloclip-skill`)",
    "PAYLOAD_TOO_LARGE": "The request is over 32MB — upload fewer or smaller images (≤5MB each, ≤6 per call)",
}

# PREDUCT_ (sic) is what Kaloclip actually returns for a full queue on script analysis
_QUEUE_FULL = ("TASK_QUEUE_FULL", "PREDUCT_CHECK_BLOCKED", "PRODUCT_CHECK_BLOCKED")

_STATUS_CODES = {403: "INSUFFICIENT_RIGHTS", 404: "NOT_FOUND", 413: "PAYLOAD_TOO_LARGE"}

_CONFLICT_HINTS = {
    "IDEMPOTENCY_KEY_CONFLICT": "This --idempotency-key was already used with different parameters — "
    "rerun with the original parameters, or drop --idempotency-key to submit a new job",
    "SUBMISSION_IN_PROGRESS": "The first submission with this key has not returned a job id yet — rerun the "
    "identical command with the same --idempotency-key in a few seconds (within 5 minutes)",
    "SUBMISSION_OUTCOME_UNKNOWN": "Kaloclip cannot tell whether the first submission went through — stop "
    "submitting automatically and tell the user; don't retry with a new key (it could bill twice)",
}


def _translate(status: int, body: dict) -> ClipError:
    # the gateway answers some rejections (path not forwarded) with an empty body
    code = str(body.get("code") or _STATUS_CODES.get(status) or f"HTTP_{status}")
    message = str(body.get("message") or "").strip()
    rid = f" (request_id {body['request_id']})" if body.get("request_id") else ""
    text = f"{code}: {message or 'request failed'}{rid}"

    if code in ("INVALID_API_KEY", "API_KEY_INVALID", "AUTHENTICATION_FAILED") or status == 401:
        if code == "INVALID_API_KEY":
            lines = ["The key is missing, wrong, expired, or disabled", *AUTH_HELP[:2]]
        else:
            lines = ["The gateway's own Kaloclip credential was refused — a problem on the KaloData side, "
                     "not with this key; report it and retry later"]
        return ClipError(text, kind="auth", help_lines=lines)
    if code in _GATEWAY_HINTS:
        kind = "auth" if code == "INSUFFICIENT_RIGHTS" else "parameter"
        return ClipError(text, kind=kind, help_lines=[_GATEWAY_HINTS[code]])
    # checked before the 400 branch: the queue rejection can come back as HTTP 400
    if code in _QUEUE_FULL:
        return ClipError(text, kind="queue", help_lines=[
            "The account's task queue is full (Kaloclip membership limit, e.g. one running job on Free) — "
            "nothing was submitted and no credits were spent; wait for the running video or analysis "
            "(`clip job <job_id> --wait`), then rerun the same command with the same --idempotency-key",
        ])
    # a model/duration/resolution that is not one `clip options` row
    if code == "VIDEO_MODEL_UNAVAILABLE" or (
            code == "ILLEGAL_ARGUMENT" and any(w in message for w in ("internalModelId", "duration", "resolution"))):
        return ClipError(text, kind="parameter", help_lines=[
            "This model/duration/resolution is not accepted now — run `clip options` and take --model, "
            "--duration and --resolution from one of its rows",
        ])
    if code in ("VALIDATION_FAILED", "INVALID_ARGUMENT") or status == 400:
        fields = _field_errors(body.get("data"))
        if fields:
            text = f"{code}: " + "; ".join(fields)
        return ClipError(text, kind="parameter",
                         help_lines=["Fix the flags behind these fields and retry (see `--help` of this command)"])
    if code == "RATE_LIMIT_EXCEEDED":
        return ClipError(text, kind="rate_limit", help_lines=[
            "Per-user rate limit (uploads/imports 10/min, product resolve 10/min, preview 20/min, videos 5/min) — "
            "wait at least 60 seconds before the next call",
        ])
    if code in _CONFLICT_HINTS:
        return ClipError(text, kind="parameter", help_lines=[_CONFLICT_HINTS[code]])
    if code == "RESOURCE_NOT_FOUND":
        return ClipError(text, kind="parameter", help_lines=[
            "Check the id: jobs are only visible to the account that created them, "
            "and script analysis results expire 30 days after creation — don't keep polling",
        ])
    if code == "UPSTREAM_UNAVAILABLE" or status >= 500:
        return ClipError(text, kind="network", help_lines=["Kaloclip is temporarily unavailable; retry shortly"])
    return ClipError(text)


def request(
    cfg: Config,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    body=None,
    files: tuple[str, list[Path]] | None = None,
    headers: dict | None = None,
    timeout: float = 60.0,
):
    """Call base_url+path; return the envelope's `data`.

    Retries (network errors, 5xx) resend the identical request, so job
    submissions must carry their Idempotency-Key in `headers`.
    """
    require_auth(cfg)
    url = cfg.base_url.rstrip("/") + path
    if params:
        query = {k: v for k, v in params.items() if v not in (None, "")}
        if query:
            url += "?" + urllib.parse.urlencode(query)
    hdrs = _headers(cfg)
    payload = None
    if files is not None:
        payload, hdrs["Content-Type"] = multipart(*files)
    elif body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json;charset=UTF-8"
    hdrs.update(headers or {})

    last = "unknown error"
    for attempt in range(_RETRIES):
        try:
            status, raw = _do_request(url, payload, hdrs, timeout, method)
            parsed = _parse(raw)
            if parsed is not None and parsed.get("success") is True:
                return parsed.get("data")
            if parsed is None or not parsed.get("code"):
                if status >= 500 or (200 <= status < 300 and parsed is None):
                    raise _Retryable(f"HTTP {status}" + ("" if parsed is not None else " (no JSON body)"))
                parsed = parsed or {}
            elif status >= 500 and parsed.get("code") == "UPSTREAM_UNAVAILABLE":
                raise _Retryable(f"HTTP {status} UPSTREAM_UNAVAILABLE")
            raise _translate(status, parsed)
        except _Retryable as e:
            last = str(e)
            if attempt < _RETRIES - 1:
                time.sleep(0.5 * (attempt + 1) + random.uniform(0, 0.3))
    raise ClipError(
        f"Kaloclip unreachable after {_RETRIES} attempts ({last})",
        kind="network",
        help_lines=["Retry shortly; check network access to " + urllib.parse.urlsplit(cfg.base_url).netloc],
    )
