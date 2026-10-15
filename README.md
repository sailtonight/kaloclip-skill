# kaloclip-skill

Agent skill for [Kaloclip](https://clip.kalowave.com) AI short-video creation — product link or images → video script → finished TikTok-style video, plus shot-by-shot analysis of existing videos.

The skill bundles a zero-dependency CLI (`clip`, Python 3.11+ stdlib only). Installing the skill is all it takes: the agent runs the CLI straight from the skill directory.

## Install

```sh
npx skills add sailtonight/kaloclip-skill               # project-level
npx skills add sailtonight/kaloclip-skill -g            # global
```

## Configure

`clip` uses the KaloData OpenAPI key — the same key as the [kalodata skill](https://github.com/sailtonight/kalodata-skill)'s `kalo` CLI, read from the same place. If `kalo` already works on this machine, there is nothing to do.

Otherwise get a key at [open-center/account](https://www.kalodata.com/open-center/account) → *generate key*, then:

```sh
export KALODATA_API_KEY=<token>      # or: clip config set --key <token>
```

The key is sent as the `secret-key` header to the KaloData gateway (`/openapi/v1/clip/**`), which identifies the account from it. Optional: `KALOCLIP_BASE_URL`, or `clip config set --base-url staging` for the staging gateway.

Jobs spend the account's credits at Kaloclip prices, under the account's Kaloclip membership (video queue size, watermark): a script draft costs 2 credits; a video costs what `clip video preview` reports for the model, duration and resolution (sd2 · 15s · 720P = 28). Everything else is free. `clip credit` shows the balance.

## Usage

```sh
alias clip="python3 skills/kaloclip/scripts/clip.py"

clip options                                   # models × durations available now
clip product resolve 'https://shop.tiktok.com/view/product/1732052189676081387?region=US'
clip script create --product-link '<link>' --model sd2 --duration 15 --description 'playful unboxing'
#   → dry run with the cost; rerun with --yes --idempotency-key <key> --wait
clip video create --script-job <job_id>        # dry run incl. credits; then --yes ...
clip video get <job_id> --wait                 # ~15 min → videoUrl
clip analysis create 'https://www.tiktok.com/@shop/video/74...'
```

Credit-spending commands (`script create`, `video create`, `analysis create`) only print the exact request on the first run; they submit when rerun with `--yes`. Every submit carries an `Idempotency-Key`, so rerunning a submit whose outcome is unknown with the same `--idempotency-key` (within 5 minutes) can't bill twice.

Output is token-efficient TOON; add `--json` for raw JSON. Exit codes: `0` success, `1` error, `2` usage error.

## Commands

| Command | Description |
|---|---|
| `clip options` | Models, durations, resolutions; which model allows which duration |
| `clip credit` | Credit balance |
| `clip product resolve <link>` | TikTok Shop / Kalodata / Amazon link → title, categories, selling points, image assets |
| `clip image upload <file...>` / `clip image import <url...>` | Images → asset ids (`--image ASSET_ID=URL`) |
| `clip script create` / `get` / `candidates` | Draft a script (2 credits); read drafts and web rewrite candidates |
| `clip video preview` / `create` / `get` | Estimate, make and fetch videos |
| `clip analysis create` / `get` | Scene / voice-over / camera breakdown of an existing video (kept 30 days) |
| `clip job <job_id>` | Any job; `--wait` polls every 5s |
| `clip config [set]` | Show or save the key (shared with `kalo`) and endpoint |

## Local MCP server

A stdio MCP server exposes the same commands as tools (`options`, `product_resolve`, `script_create`, `video_create`, `job`, …), sharing the CLI's client and validation. Tool arguments use flag names without `--`, e.g. `video_preview({"model":"sd2","duration":15})`; list positionals are plural (`image_import({"urls":[...]})`). Credit-spending tools return a `dry_run` until called again with `yes: true` and the returned `idempotency_key`. Configuration is not exposed as a tool.

```json
{
  "mcpServers": {
    "kaloclip": {
      "command": "uvx",
      "args": ["--from", "kaloclip-mcp[mcp] @ git+https://github.com/sailtonight/kaloclip-skill", "kaloclip-mcp"],
      "env": { "KALODATA_API_KEY": "YOUR_API_KEY" }
    }
  }
}
```

From a checkout: `uv run --directory /absolute/path/to/kaloclip-skill --extra mcp kaloclip-mcp`.

## Development

```sh
uv run --with pytest pytest -q                 # against a local gateway mock, no credentials needed
uv run --extra mcp --with pytest pytest -q     # includes MCP protocol and subprocess tests
```

## License

MIT
