# VLM gateway

The Toolbox in gateway mode, deployed next to llama-server. It is the same
package and the same image as the Toolbox, started with
`acessilia_toolbox.api.gateway_app:create_gateway_app` instead of
`create_app`: it serves only `GET /v1/health`, `GET /v1/vlm/health` and the
chat, at `POST /v1/vlm/chat/completions` and `POST /v1/vlm/v1/chat/completions`
(no `/v1/docs` or `/v1/openapi.json`). The second path makes `/v1/vlm` look
like llama-server's root to the environments' Toolboxes: their provider posts
`{VLM_SERVE_URL}/v1/chat/completions` and checks `{VLM_SERVE_URL}/health`.

Run it as **one process**. The limiter is a semaphore inside the process, so
`uvicorn --workers N` would let N times `max_concurrency` requests reach
llama-server, the problem the gateway exists to prevent. Do not add `--workers`.

```
 staging host                      production host
 core ──► Toolbox                  core ──► Toolbox
             │ qwen3-vl-8b                    │ qwen3-vl-8b
             └──────────────┐  ┌──────────────┘
                            ▼  ▼
 model server host   VLM gateway :8002   one limiter, max_concurrency = --parallel
                            │
                            ▼ 127.0.0.1:8080
                     llama-server --parallel 2
```

Why: each Toolbox limits its own requests to the model server. Two
environments sharing one server each believed they had all of its slots, so
together they sent twice as many requests as it has. The gateway is the one
client of the server, so one limiter holds every environment to its slots, and
with llama-server on loopback nothing can reach the server around it.

## Files

| File | Where it goes |
|---|---|
| `docker-compose.yml`, `providers-config.yaml`, `.env.example` | `/opt/vlm-gateway/` on the model server's host |
| `llama-server.service` | `/etc/systemd/system/` (the server's unit, versioned here) |
| `vlm-gateway.service` | `/etc/systemd/system/`, only to run the gateway without Docker |

## Timeouts

Each layer must wait longer than the one behind it, or it gives up before the
answer arrives (`docs/api.md`, "VLM chat gateway"):

| Layer | Must wait longer than | Setting |
|---|---|---|
| Gateway → llama-server | | `VLM_TIMEOUT_SECONDS=600`, `VLM_SLOT_WAIT_SECONDS=1800` (gateway `.env`) |
| Environment Toolbox → gateway | 1800 + 600 | `VLM_TIMEOUT_SECONDS=2700` |
| Environment Toolbox, its own slot wait | | `VLM_SLOT_WAIT_SECONDS=120` |
| Core → environment Toolbox | 120 + 2700 | core `MODEL_CALL_TIMEOUT_SECONDS=3000` |

The environment Toolbox must not queue on its own: give it a larger limit than
the gateway (`VLM_MAX_CONCURRENCY=4`), so it only relays and the gateway holds
the queue. Its own slot wait still counts in the worst case: with the default
1800 s, a fifth concurrent request would wait 1800 + 2700 = 4500 s, past the
core's 3000 s. Set it small (120 s), so the chain holds as a bound, not as a
likelihood, and a local wait that runs out answers 504 quickly.

## Deploy

Switch without downtime, staging first:

1. **Start the gateway against the current server.** In `/opt/vlm-gateway`,
   `cp .env.example .env && chmod 600 .env`, fill in `TOOLBOX_API_KEY` (new) and
   `VLM_API_KEY` (llama-server's), and set `LLAMA_SERVER_URL` to the server's
   current address (`http://172.16.109.33:81`). Then:

   ```bash
   docker compose up -d
   curl -s http://172.16.109.33:8002/v1/health
   curl -s -H "Authorization: Bearer <gateway TOOLBOX_API_KEY>" \
     http://172.16.109.33:8002/v1/vlm/health
   ```

   The second call answers `"status": "healthy"` once the gateway reaches
   llama-server. If the GHCR image is private, `docker login ghcr.io` first.

2. **Hold the server's slots during the switch.** While staging goes through
   the gateway and production still calls llama-server directly, both limit
   only their own requests: the gateway's 2 plus production's 2 would send
   four requests to the two slots. Until production moves too, split the slots:
   set `VLM_MAX_CONCURRENCY=1` in the gateway's `.env` (`docker compose up -d`)
   and in production's Toolbox `.env` (recreate it).

3. **Point staging's Toolbox at the gateway.** In its `.env`:

   ```bash
   VLM_SERVE_URL=http://172.16.109.33:8002/v1/vlm
   VLM_API_KEY=<gateway TOOLBOX_API_KEY>
   VLM_TIMEOUT_SECONDS=2700
   VLM_MAX_CONCURRENCY=4
   VLM_SLOT_WAIT_SECONDS=120
   ```

   Recreate it (`docker compose up -d toolbox`), raise the core's
   `MODEL_CALL_TIMEOUT_SECONDS` to 3000 and recreate the core. Check
   `GET /v1/providers/qwen3-vl-8b/health` on that Toolbox and process a
   document.

4. **Move production, then give the gateway the server's slots.** Do step 3
   for production. Then set the gateway back to `VLM_MAX_CONCURRENCY=2` and
   `docker compose up -d`. Only now, with both environments behind the
   gateway, is `requests_processing` on llama-server's `/metrics` staying at 2
   or below the test that the gateway holds the server: during the switch it
   shows what the split of step 2 allows.

5. **Close llama-server to everything but the gateway.** Install
   `llama-server.service` (it listens on `127.0.0.1:8080`), set
   `LLAMA_SERVER_URL=http://127.0.0.1:8080` in the gateway's `.env`, and:

   ```bash
   systemctl daemon-reload && systemctl restart llama-server
   docker compose up -d
   ```

   From any other host, `curl http://172.16.109.33:81/v1/models` must now
   fail to connect.

6. **Restrict the gateway's port** to the environments' hosts, for example
   `ufw allow from <staging host> to any port 8002` and the same for
   production.

## Update and roll back

The environments depend only on the gateway's HTTP contract (the OpenAI chat
API and `/v1/vlm/health`), so the gateway updates on its own schedule:

```bash
# edit TOOLBOX_IMAGE_TAG in .env
docker compose pull && docker compose up -d
```

Roll back by restoring the previous tag. Requests in flight during the restart
fail with 503 at the environments' Toolboxes.

## Without Docker

Install the Toolbox at a pinned release into a virtualenv and run
`vlm-gateway.service` (instructions in the unit file). The `.env` and
`providers-config.yaml` are the same.
