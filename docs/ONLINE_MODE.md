# Online mode

The plugin has two modes (Settings tab in the GODMODE widget):

| Mode | What answers | Needs |
|------|--------------|-------|
| **Offline** | The built-in offline engine inside the plugin: keyword routing + verified templates, no neural model. | Nothing. |
| **Online** | The GODMODE server (`python -m godmode.server`): the trained model with thinking levels, the tools, and retrieval. | A running server and its URL (+ token). |

If the server cannot be reached, the plugin falls back to offline and says so in the chat.
It never pretends the model answered.

"Online" means *the plugin talks to a GODMODE server*. That server can run on your own PC
(easiest, free) or on a host on the internet. Nothing is hosted for you: I cannot deploy it
on your behalf, so pick one of the options below.

## Option A: your own PC (recommended)

```bash
cd godmode                      # the folder from the zip
pip install torch numpy         # once
python -m godmode.server        # picks runs/sft-scripts/best.pt automatically
```

In Studio: *Game Settings > Security > Allow HTTP Requests* = on. In the plugin's
Settings: mode **Online**, URL `http://127.0.0.1:8765`, token empty, press **Test connection**.

Thinking levels on a 4-core CPU (rough): Low a few seconds, Medium ~10 s, High ~20 s,
Extra high ~40 s, Max can exceed a minute.

## Option B: a host on the internet (Docker)

The repo ships a `Dockerfile`. On any public bind the server **refuses to start without a
token**, so set `GODMODE_TOKEN` to a long random string and put the same string in the
plugin's Token field.

```bash
docker build -t godmode .
docker run -p 8765:8765 -e GODMODE_TOKEN=<long-random-string> godmode
```

### Hugging Face Spaces (free CPU tier)

1. Create a Space, SDK **Docker**.
2. Upload this repo's files plus `runs/sft-scripts/best.pt` (≈50 MB).
3. Add to the Space's `README.md` front matter: `app_port: 7860`.
4. Space settings > *Variables and secrets*: secret `GODMODE_TOKEN`, variable `PORT=7860`.
5. Plugin URL: `https://<user>-<space>.hf.space`, Token: the same secret.

Any host that runs a Docker image and sets `PORT` (Render, Railway, Fly.io, a VPS) works
the same way. A free CPU host is slower than a modern PC, so prefer Low/Medium there.

## Environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `GODMODE_HOST` | `127.0.0.1` | bind address (`0.0.0.0` in the Docker image) |
| `PORT` | `8765` | port |
| `GODMODE_TOKEN` | unset | required header `X-Godmode-Token`; mandatory for non-local binds |
| `GODMODE_CKPT` | auto | checkpoint path; default `runs/sft-scripts/best.pt`, else `runs/small-full2/best.pt` |

## Security notes

* The server never changes your place; it only proposes actions. Model-written scripts
  always need your approval in the plugin, whatever the autonomy level.
* Use HTTPS for anything on the internet (Spaces and most hosts give you HTTPS).
* `--allow-no-token` exists for testing only; do not use it on a public host.
