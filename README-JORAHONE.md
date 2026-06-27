# JorahOne AI Stack

Single-node self-hosted AI stack for a GTX 3060 (12GB VRAM) / 30GB RAM Ubuntu box.

Two inference backends share the GPU:

- **`llama-server`** — always-on backend for your **Hermes agent**. Native llama.cpp
  tool-calling, 65k context, quantized KV cache.
- **`Ollama`** — on-demand backend for the **IT team's Open WebUI**. Cold-loads on
  first request, unloads after 5 min idle, so it never permanently competes with
  Hermes for VRAM.

Both are unified behind **LiteLLM**, so every downstream service (Open WebUI,
Honcho) talks to one OpenAI-compatible endpoint regardless of which engine answers.

## VRAM budget (12GB GTX 3060)

| Component | ~VRAM |
|---|---|
| Hermes-3-Llama-3.1-8B (Q5_K_M) | 5.7 GB |
| KV cache @ 65k ctx (q4_0 quant) | 2.5 GB |
| llama.cpp overhead | ~1.0 GB |
| **Hermes total (always-on)** | **~9.2 GB** |
| Qwen2.5-7B-Instruct (Q4_K_M), when team is active | ~4.5 GB |

Hermes' ~9.2GB is permanently reserved. The team's model only loads into the
remaining ~2.8GB headroom on demand — if both are hit at the exact same moment,
Ollama queues briefly rather than crashing (set `OLLAMA_NUM_PARALLEL=2` and keep an eye
on it; drop to a smaller team model if you see OOM in `docker logs ollama`).

## Stack

| Service | Port | Role |
|---|---|---|
| llama-server | 8081 | Hermes inference backend |
| Ollama | 11434 | Team inference backend |
| LiteLLM | 4000 | Unified router |
| Open WebUI | 3000 | Team chat frontend |
| Honcho API | 8000 | Shared memory layer (namespaced, see `docs/honcho-multitenancy.md`) |
| Qdrant | 6333 | Vector store |
| SearXNG | 8888 | Private search for agents |
| CostForge | 8090 | Cost/pricing dashboard, ingests Hermes/OpenRouter/Telegram usage |
| Caddy | 80/443 | Reverse proxy |

Notion replaces Standard Notes as an **API integration**, not a container — see
`docs/honcho-multitenancy.md` for wiring details.

## Setup

```bash
git clone <this-repo>
cd jorahone-ai-stack
cp .env.example .env        # fill in passwords/keys
./scripts/download-models.sh
docker compose up -d
```

Then:

1. Open WebUI: `http://<tailscale-ip>:3000` — create accounts for team members.
2. Verify Hermes: `curl http://localhost:8081/v1/models`
3. Pull the team model if the script didn't auto-pull:
   `docker exec ollama ollama pull qwen2.5:7b-instruct-q4_K_M`
4. Confirm LiteLLM sees both: `curl http://localhost:4000/v1/models -H "Authorization: Bearer $LITELLM_MASTER_KEY"`

## Tailscale

All ports above are meant to be reached over your tailnet only — don't expose
them publicly. Bind Caddy to your Tailscale hostname (`tailscale status` to find
it) and use MagicDNS so `http://<machine-name>:3000` works from any device on
your tailnet.

## Requirements

- NVIDIA Container Toolkit installed (`nvidia-ctk` configured for Docker)
- Docker + Docker Compose v2
- ~10GB disk for the Hermes GGUF, more for Ollama model cache
- Tailscale running on the host

## Notes

- `honcho-api` and `honcho-deriver` build directly from the upstream
  `plastic-labs/honcho` repo per your earlier decision to stay on Honcho rather
  than migrate to mem0.
- `costforge` builds from `OneByJorah/CostForge` at `docker compose build` time
  (cloned fresh each build, no upstream Dockerfile required). Its Hermes adapter
  is pointed at `llama-server` directly; its OpenRouter adapter is pointed at
  LiteLLM instead, so it can pick up cost data for any cloud model you add later
  too. Telegram ingest stays off unless you set `COSTFORGE_TELEGRAM_BOT_TOKEN`.
- If you add more team-facing models later, add them to `litellm/config.yaml`
  under `model_list` — no other service needs to change.
