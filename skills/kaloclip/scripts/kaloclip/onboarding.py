"""First-run guide, shown wherever the API key turns out to be missing.

The Setup section of the SKILL.md next to this package mirrors it — keep both
in sync.
"""

from __future__ import annotations

KEY_URL = "https://www.kalodata.com/open-center/account"
SIGNUP_URL = "https://www.kalodata.com/open-center/home"
CLIP_URL = "https://clip.kalowave.com"

SETUP_LINES = [
    f'1. Get a key: sign in at {KEY_URL} and click "generate key" '
    f"(no account yet? register at {SIGNUP_URL}). It is the KaloData OpenAPI key — "
    "if `kalo` (the kalodata skill) already works on this machine, the key is already set.",
    "2. Save it: `clip config set --key <API_KEY>`, or set KALODATA_API_KEY in the environment "
    "(shared with `kalo`, stored in ~/.config/kalodata/config.toml).",
    "3. Credits: script drafts, videos and script analysis spend the account's credits at "
    f"Kaloclip prices, under Kaloclip membership rules (queue size, watermark) — see {CLIP_URL}. "
    "`clip credit` shows the balance.",
]

NO_KEY_MESSAGE = "no KaloData API key configured yet"
