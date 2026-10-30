# VLM gateway

The Toolbox in gateway mode, deployed next to llama-server. It is the same
package and the same image as the Toolbox, started with
`acessilia_toolbox.api.gateway_app:create_gateway_app` instead of
`create_app`: it serves only `GET /v1/health`, `GET /v1/vlm/health` and
`POST /v1/vlm/chat/completions`.

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
| Gateway → llama-server | | `timeout_seconds` 600, `slot_wait_seconds` 1800 |
| Environment Toolbox → gateway | 1800 + 600 | `VLM_TIMEOUT_SECONDS=2700` |
| Core → environment Toolbox | the Toolbox's slot wait + 2700 | core `MODEL_CALL_TIMEOUT_SECONDS` |

The environment Toolbox must not queue on its own: give it a larger limit than
the gateway (`VLM_MAX_CONCURRENCY=4`), so it only relays and the gateway holds
the queue. With that limit it rarely waits for a slot, so its own
`slot_wait_seconds` matters little; the core's timeout of 3000 s or more covers
a short wait there.

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

2. **Point an environment's Toolbox at the gateway.** In its `.env`:

   ```bash
   VLM_SERVE_URL=http://172.16.109.33:8002/v1/vlm
   VLM_API_KEY=<gateway TOOLBOX_API_KEY>
   VLM_TIMEOUT_SECONDS=2700
   VLM_MAX_CONCURRENCY=4
   ```

   Recreate it (`docker compose up -d toolbox`), raise the core's
   `MODEL_CALL_TIMEOUT_SECONDS` to 3000 and recreate the core. Check
   `GET /v1/providers/qwen3-vl-8b/health` on that Toolbox, process a document,
   and watch `requests_processing` on llama-server's `/metrics` stay at 2 or
   below. Then do the same for production.

3. **Close llama-server to everything but the gateway.** Install
   `llama-server.service` (it listens on `127.0.0.1:8080`), set
   `LLAMA_SERVER_URL=http://127.0.0.1:8080` in the gateway's `.env`, and:

   ```bash
   systemctl daemon-reload && systemctl restart llama-server
   docker compose up -d
   ```

   From any other host, `curl http://172.16.109.33:81/v1/models` must now
   fail to connect.

4. **Restrict the gateway's port** to the environments' hosts, for example
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
