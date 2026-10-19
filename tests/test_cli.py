import json

import pytest

from kaloclip.cli import main

LINK = "https://shop.tiktok.com/view/product/1732052189676081387?region=US"
IMG = "739230=https://resource.kaloclip.com/a.png"
PRODUCT = {
    "productId": "1732052189676081387", "country": "US", "language": "en",
    "productTitle": "Body Care Set", "categoryNames": ["Beauty", "Body Care"],
    "sellingPoints": ["Exfoliating"],
    "imageInfos": [{"assetId": 1, "imageUrl": "https://r/1.png", "description": "Front"},
                   {"assetId": 2, "imageUrl": "https://r/2.png", "description": None}],
}


def ok(data):
    return {"success": True, "code": None, "message": None, "data": data}


def fail(code, message, data=None):
    return {"success": False, "code": code, "message": message, "data": data}


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr().out


def posts(server, path):
    return [r for r in server.requests if r[0] == "POST" and r[1] == path]


# --- setup -------------------------------------------------------------------

def test_home_without_key_shows_setup(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("KALODATA_API_KEY", raising=False)
    monkeypatch.setenv("KALODATA_CONFIG_DIR", str(tmp_path))
    code, out = run(capsys)
    assert code == 0
    assert "commands[" in out
    assert "auth: not configured" in out
    assert "setup[3]:" in out and "open-center/account" in out


def test_call_without_key_is_actionable_and_sends_nothing(capsys, env, monkeypatch):
    monkeypatch.delenv("KALODATA_API_KEY")
    code, out = run(capsys, "credit")
    assert code == 1
    assert "no KaloData API key configured yet" in out
    assert "clip config set --key" in out
    assert env.requests == []


def test_key_is_shared_with_kalo_config(capsys, env, monkeypatch, tmp_path):
    monkeypatch.delenv("KALODATA_API_KEY")
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config.toml").write_text('api_key = "from-kalo"\nregion = "GB"\n')
    run(capsys, "config", "set", "--base-url", "staging")
    text = (cfg / "config.toml").read_text()
    assert 'region = "GB"' in text and 'api_key = "from-kalo"' in text
    assert 'clip_base_url = "https://staging.kalodata.com/openapi/v1/clip"' in text
    code, out = run(capsys, "config")
    assert "set (…kalo)" in out


def test_headers_never_carry_a_user_id(capsys, env):
    env.responses["/credits"] = ok({"totalRemain": 160.8, "monthlyRemain": 149.6, "permanentRemain": 11.2,
                                    "subAccount": False})
    code, out = run(capsys, "credit")
    assert code == 0
    assert "total_remain: 160.8" in out
    _, path, _, headers, _ = env.requests[0]
    assert headers["secret-key"] == "test-key"
    assert headers["source-type"] == "SKILL"
    assert "x-user-id" not in headers and "x-internal-api-key" not in headers


# --- free calls --------------------------------------------------------------

def test_options_lists_combinations_with_credits(capsys, env):
    env.responses["/videos/options"] = ok({
        "aspectRatios": ["RATIO_9_16", "RATIO_16_9"],
        "models": [
            {"internalModelId": "giv15", "name": "Grok Imagine 1.5", "combinations": [
                {"duration": 8, "resolution": "720P", "credits": 10},
                {"duration": 15, "resolution": "1080P", "credits": 12},
            ]},
            {"internalModelId": "off", "name": "Empty", "combinations": []},
        ],
    })
    code, out = run(capsys, "options", "--language", "th")
    assert code == 0
    assert "combinations[2]{model,name,duration,resolution,credits}:" in out
    assert "giv15,Grok Imagine 1.5,8,720P,10" in out and "giv15,Grok Imagine 1.5,15,1080P,12" in out
    assert 'ratios[2]: "9:16","16:9"' in out
    assert env.requests[0][2] == {"video-language": "th"}


def test_options_without_models(capsys, env):
    env.responses["/videos/options"] = ok({"aspectRatios": ["RATIO_9_16"], "models": []})
    code, out = run(capsys, "options")
    assert code == 0
    assert "combinations: 0 results" in out


def test_options_legacy_shape_pairs_models_with_durations(capsys, env):
    env.responses["/videos/options"] = ok({
        "modelOptions": [{"value": "sd2", "label": "sd2_label"}, {"value": "sr2l", "label": "sr2l_label"}],
        "durationOptions": [{"label": "15 S", "value": 15, "default": True}],
        "resolutionOptions": [{"value": "720P"}],
        "aspectRatioOptions": [{"label": "9:16", "value": "RATIO_9_16"}],
        "rules": {"duration_model": {"15": ["sd2"]}},
    })
    code, out = run(capsys, "options", "--language", "en")
    assert code == 0
    assert "models[2]{model,durations}:" in out
    assert 'sd2,"15"' in out and "sr2l,null" in out
    assert "sr2l not in the duration rules" in out
    assert env.requests[0][2] == {"video-language": "en"}


def test_image_upload_is_multipart(capsys, env, tmp_path):
    img = tmp_path / "front.png"
    img.write_bytes(b"\x89PNG fake")
    env.responses["/images"] = ok([{"assetId": 7, "imageUrl": "https://r/7.png", "success": True}])
    code, out = run(capsys, "image", "upload", str(img))
    assert code == 0
    assert "--image 7=https://r/7.png" in out
    _, _, _, headers, body = env.requests[0]
    assert headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="images"; filename="front.png"' in body and b"\x89PNG fake" in body


def test_image_upload_checks_files_locally(capsys, env, tmp_path):
    big = tmp_path / "big.png"
    big.write_bytes(b"0" * (5 * 1024 * 1024 + 1))
    code, out = run(capsys, "image", "upload", str(big))
    assert code == 2 and "limit is 5 MB" in out
    code, out = run(capsys, "image", "upload", str(tmp_path / "missing.png"))
    assert code == 2 and "no such file" in out
    assert env.requests == []


def test_image_import_partial_failure(capsys, env):
    env.responses["/images/import"] = ok([
        {"assetId": 9, "imageUrl": "https://r/9.jpg", "success": True},
        {"assetId": None, "imageUrl": "", "success": False},
    ])
    code, out = run(capsys, "image", "import", "https://a/1.jpg", "https://a/2.jpg")
    assert code == 0
    assert "ok=false were not stored" in out
    assert env.requests[0][4] == ["https://a/1.jpg", "https://a/2.jpg"]


# --- dry runs and submits ------------------------------------------------------

def test_script_create_dry_run_sends_nothing_billable(capsys, env):
    env.responses["/products/resolve"] = ok(PRODUCT)
    code, out = run(capsys, "script", "create", "--product-link", LINK, "--model", "sd2", "--duration", "15")
    assert code == 0
    assert out.startswith("dry_run:")
    assert "credits: 2" in out
    assert "productTitle: Body Care Set" in out
    assert "--yes --idempotency-key clip-" in out
    assert posts(env, "/scripts") == []  # only the free resolve went out


def test_script_create_yes_submits_waits_and_remembers(capsys, env):
    env.responses["/products/resolve"] = ok(PRODUCT)
    env.responses["/scripts"] = ok(4242)
    env.responses["/jobs"] = [
        ok({"id": 4242, "jobType": "REFERENCE_SCRIPT_GENERATE", "state": "processing"}),
        ok({"id": 4242, "jobType": "REFERENCE_SCRIPT_GENERATE", "state": "completed",
            "output": {"script": "Scene 1\nHook"}}),
    ]
    code, out = run(capsys, "script", "create", "--product-link", LINK, "--title", "My title",
                    "--model", "sd2", "--duration", "15", "--yes", "--idempotency-key", "k-1", "--wait")
    assert code == 0, out
    assert "state: completed" in out
    assert "script[12 chars]: |\n  Scene 1\n  Hook" in out
    (_, _, _, headers, body), = posts(env, "/scripts")
    assert headers["idempotency-key"] == "k-1"
    assert body["creditSourceType"] == "OPENAPI"
    assert body["productTitle"] == "My title"  # explicit flag beats the resolved product
    assert body["categoryNames"] == ["Beauty", "Body Care"]
    assert body["imageInfos"] == [{"assetId": 1, "imageUrl": "https://r/1.png"},
                                  {"assetId": 2, "imageUrl": "https://r/2.png"}]
    assert body["language"] == "en" and body["duration"] == 15

    # the remembered inputs feed `video create --script-job`
    env.responses["/scripts"] = ok({"id": 4242, "state": "completed", "jobType": "REFERENCE_SCRIPT_GENERATE",
                                    "output": {"script": "Scene 1\nHook"}, "input": {"creditSourceType": "OPENAPI"}})
    env.responses["/videos/preview"] = ok(28)
    code, out = run(capsys, "video", "create", "--script-job", "4242", "--resolution", "1080P")
    assert code == 0, out
    assert "credits: 28" in out and "productTitle: My title" in out
    assert posts(env, "/videos") == []
    preview = posts(env, "/videos/preview")[-1][4]
    assert preview == {"aspectRatio": "RATIO_9_16", "duration": 15, "resolution": "1080P",
                       "internalModelId": "sd2", "quantity": 1}


def test_video_create_yes(capsys, env, tmp_path):
    script = tmp_path / "s.txt"
    script.write_text("Edited script\n")
    env.responses["/videos"] = ok(777)
    code, out = run(capsys, "video", "create", "--script-file", str(script), "--title", "T", "--image", IMG,
                    "--model", "sd2", "--duration", "15", "--ratio", "16:9", "--script-type", "FIDELITY", "--yes")
    assert code == 0, out
    assert "job_id: 777" in out and "clip video get 777 --wait" in out
    (_, _, _, headers, body), = posts(env, "/videos")
    assert headers["idempotency-key"].startswith("clip-")
    assert body["script"] == "Edited script" and body["scriptType"] == "FIDELITY"
    assert body["aspectRatio"] == "RATIO_16_9" and body["quantity"] == 1
    assert body["imageInfos"] == [{"assetId": 739230, "imageUrl": "https://resource.kaloclip.com/a.png"}]
    assert posts(env, "/videos/preview") == []  # no estimate once the user confirmed


def test_script_job_reuses_the_drafts_resolution(capsys, env):
    env.responses["/scripts"] = [ok(7), ok({"id": 7, "state": "completed", "jobType": "REFERENCE_SCRIPT_GENERATE",
                                           "output": {"script": "S"}})]
    run(capsys, "script", "create", "--image", IMG, "--title", "t", "--category", "c", "--model", "giv15",
        "--duration", "15", "--resolution", "1080P", "--yes")
    (_, _, _, _, body), = posts(env, "/scripts")
    assert "resolution" not in body  # /scripts takes no resolution
    run(capsys, "video", "create", "--script-job", "7")
    (_, _, _, _, preview), = posts(env, "/videos/preview")
    assert preview["resolution"] == "1080P"
    run(capsys, "video", "create", "--script-job", "7", "--resolution", "720P")
    assert posts(env, "/videos/preview")[-1][4]["resolution"] == "720P"  # an explicit flag wins


def test_dry_run_needs_a_key(capsys, env, monkeypatch):
    monkeypatch.delenv("KALODATA_API_KEY")
    code, out = run(capsys, "analysis", "create", "https://www.tiktok.com/@x/video/1")
    assert code == 1
    assert "no KaloData API key" in out and "dry_run" not in out


def test_script_job_from_elsewhere_needs_inputs(capsys, env):
    env.responses["/scripts"] = ok({"id": 5, "state": "completed", "output": {"script": "x"}})
    code, out = run(capsys, "video", "create", "--script-job", "5")
    assert code == 2
    assert "not submitted from this machine" in out


def test_script_job_not_ready(capsys, env):
    env.responses["/scripts"] = ok({"id": 5, "state": "processing"})
    code, out = run(capsys, "video", "create", "--script-job", "5", "--title", "T", "--image", IMG,
                    "--model", "sd2", "--duration", "15")
    assert code == 1
    assert "is processing" in out and "clip script get 5 --wait" in out


def test_analysis_create(capsys, env):
    env.responses["/script-analysis"] = ok(99)
    code, out = run(capsys, "analysis", "create", "https://www.tiktok.com/@x/video/1", "--commerce",
                    "--country", "US", "--yes")
    assert code == 0, out
    (_, _, _, _, body), = posts(env, "/script-analysis")
    assert body == {"creditSourceType": "OPENAPI", "videoUrl": "https://www.tiktok.com/@x/video/1",
                    "country": "US", "commerce": True}


def test_analysis_dry_run_shows_credits(capsys, env):
    code, out = run(capsys, "analysis", "create", "https://www.tiktok.com/@x/video/1")
    assert code == 0
    assert out.startswith("dry_run:") and "credits: 1" in out
    assert env.requests == []


@pytest.mark.parametrize("argv,needle", [
    (["script", "create", "--image", "nope", "--title", "t", "--category", "c", "--model", "m", "--duration", "15"],
     "--image expects ASSET_ID=IMAGE_URL"),
    (["script", "create", "--image", IMG, "--title", "t", "--category", "c", "--model", "m", "--duration", "0"],
     "--duration must be a positive number"),
    (["script", "create", "--image", IMG, "--model", "m", "--duration", "15"], "missing: --title, --category"),
    (["video", "create", "--title", "t", "--image", IMG, "--model", "m", "--duration", "15"],
     "missing the final script"),
    (["video", "preview", "--model", "sd2"], "missing required flag(s): --duration"),
    (["analysis", "create", "https://www.tiktok.com/@x/video/1", "--commerce"], "--commerce needs --country"),
    (["job", "abc"], "job ids are numeric"),
    (["script", "create", "--bogus"], "unknown flag --bogus"),
])
def test_usage_errors_send_nothing(capsys, env, argv, needle):
    code, out = run(capsys, *argv)
    assert code == 2
    assert needle in out
    assert env.requests == []


# --- jobs ----------------------------------------------------------------------

def test_job_wait_stops_at_timeout(capsys, env, monkeypatch):
    from kaloclip.commands import common

    monkeypatch.setattr(common, "POLL_SECONDS", 1000.0)  # the next poll would overshoot --timeout
    env.responses["/jobs"] = ok({"id": 1, "state": "queued", "jobType": "VIDEO_CREATION"})
    code, out = run(capsys, "job", "1", "--wait", "--timeout", "1")
    assert code == 0
    assert "Still queued after --timeout" in out
    assert len(env.requests) == 1


def test_completed_video(capsys, env):
    env.responses["/videos"] = ok({"id": 3, "state": "completed", "jobType": "VIDEO_CREATION",
                                   "gmtCreated": 1792032517222,
                                   "output": {"videoUrl": "https://v/3.mp4", "userAssetId": 12}})
    code, out = run(capsys, "video", "get", "3")
    assert code == 0
    assert "videoUrl: \"https://v/3.mp4\"" in out
    assert 'created: "2026-10-15T02:48:37Z"' in out
    assert env.requests[0][2] == {"jobId": "3"}


def test_old_video_falls_back_to_enhance_url(capsys, env):
    env.responses["/jobs"] = ok({"id": 4, "state": "completed", "jobType": "VIDEO_CREATION",
                                 "output": {"videoUrl": None, "enhanceVideoUrl": "https://v/4e.mp4"}})
    code, out = run(capsys, "job", "4")
    assert code == 0
    assert 'videoUrl: "https://v/4e.mp4"' in out and "enhanceVideoUrl" not in out


@pytest.mark.parametrize("error_code,needle", [
    ("IMAGE_REAL_PERSON", "replace it"),
    ("SCRIPT_TOO_LONG", "shorten it"),
    (None, "Kaloclip failed on its side"),
    ("SOMETHING_NEW", "Kaloclip failed on its side"),
])
def test_failed_job_hint_follows_error_code(capsys, env, error_code, needle):
    env.responses["/jobs"] = ok({"id": 5, "state": "failed", "jobType": "VIDEO_CREATION",
                                 "errorCode": error_code, "errorMessage": "normalized reason"})
    code, out = run(capsys, "job", "5")
    assert code == 0
    assert needle in out and "refunded" in out
    assert (f"error_code: {error_code}" in out) == bool(error_code)


def test_candidates(capsys, env):
    env.responses["/script-generations/resolve"] = ok({
        "state": "completed", "scriptType": "FIDELITY",
        "scripts": [{"scriptIndex": 1, "script": "A"}, {"scriptIndex": 2, "script": "B"}],
    })
    code, out = run(capsys, "script", "candidates", "8")
    assert code == 0
    assert "scripts[2]{index,script}:" in out and "--script-type FIDELITY" in out


# --- error translation -----------------------------------------------------------

@pytest.mark.parametrize("status,body,kind_text", [
    (401, {"code": "INVALID_API_KEY", "message": "invalid secret-key", "request_id": "r1"},
     "INVALID_API_KEY: invalid secret-key (request_id r1)"),
    (403, {"code": "INSUFFICIENT_RIGHTS", "message": "api key not in whitelist", "request_id": "r2"},
     "not enabled for Kaloclip"),
    (404, None, "does not forward this path"),
    (200, fail("RATE_LIMIT_EXCEEDED", "Too many requests"), "wait at least 60 seconds"),
    (200, fail("RESOURCE_NOT_FOUND", "ai job not found: 1"), "don't keep polling"),
    (401, fail("AUTHENTICATION_FAILED", "Authentication failed"), "not with this key"),
])
def test_error_translation(capsys, env, status, body, kind_text):
    env.responses["/jobs"] = (status, body)
    code, out = run(capsys, "job", "1")
    assert code == 1
    assert out.startswith("error: ")
    assert kind_text in out
    assert len(env.requests) == 1  # 4xx and business failures are not retried


@pytest.mark.parametrize("code,message", [
    ("ILLEGAL_ARGUMENT", "internalModelId=giv15 does not match duration=30"),
    ("VIDEO_MODEL_UNAVAILABLE", "This model is temporarily unavailable. Please choose another model."),
])
def test_combination_not_in_options(capsys, env, code, message):
    env.responses["/videos/preview"] = fail(code, message)
    code_, out = run(capsys, "video", "preview", "--model", "giv15", "--duration", "30")
    assert code_ == 1
    assert message in out and "take --model, --duration and --resolution from one of its rows" in out


def test_validation_failed_lists_fields(capsys, env):
    env.responses["/videos"] = (400, fail("VALIDATION_FAILED", "x", {"script": "must not be blank"}))
    code, out = run(capsys, "video", "create", "--script", "s", "--title", "t", "--image", IMG,
                    "--model", "sd2", "--duration", "15", "--yes")
    assert code == 1
    assert "VALIDATION_FAILED: script: must not be blank" in out


def test_queue_full(capsys, env):
    env.responses["/videos"] = fail("TASK_QUEUE_FULL", "queue full")
    code, out = run(capsys, "video", "create", "--script", "s", "--title", "t", "--image", IMG,
                    "--model", "sd2", "--duration", "15", "--yes")
    assert code == 1
    assert "no credits were spent" in out


def test_upstream_outage_is_retried_with_the_same_key(capsys, env):
    env.responses["/scripts"] = (502, {"code": "UPSTREAM_UNAVAILABLE", "message": "down", "request_id": "r"})
    code, out = run(capsys, "script", "create", "--image", IMG, "--title", "t", "--category", "c",
                    "--model", "sd2", "--duration", "15", "--yes", "--idempotency-key", "same")
    assert code == 1
    sent = posts(env, "/scripts")
    assert len(sent) == 3 and {r[3]["idempotency-key"] for r in sent} == {"same"}
    assert "outcome is unknown" in out and "--idempotency-key same" in out


def test_json_mode_is_raw(capsys, env):
    env.responses["/credits"] = ok({"totalRemain": 1.5})
    code, out = run(capsys, "credit", "--json")
    assert json.loads(out) == {"totalRemain": 1.5}


def test_bad_flags_fail_before_the_product_resolve(capsys, env):
    code, out = run(capsys, "script", "create", "--product-link", LINK, "--model", "sd2", "--duration", "0")
    assert code == 2
    assert env.requests == []
