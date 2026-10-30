# Comfy Cloud / "Comfy API" — what is actually there, and what it costs us

Researched 2026-10-30 against the live service and the official docs. Every claim below is
either a quoted doc line with a URL or a `curl` I ran from this machine (output included).

**Verdict for this app: `COMFY_BASE_URL=https://cloud.comfy.org` + `COMFY_API_KEY=<key>` is
NOT a drop-in switch today.** The auth header our client already sends (`X-API-Key`) is
correct for the cloud v1 surface, but **every path is prefixed with `/api`**, `/history/{id}`
does not exist there, and `/view` answers `302` to a signed storage URL instead of bytes.
Section 6 is the exact adapter (≈25 lines in `server/cinema/comfy.py`), section 7 is the
honest list of what cloud cannot run at all (our node pack).

---

## 1. Two APIs live on the same host

| Surface | Base URL | Auth header | Status |
|---|---|---|---|
| **v1 Cloud API** | `https://cloud.comfy.org` | `X-API-Key: <key>` | **Deprecated**, still served |
| **Comfy API v2** | `https://cloud.comfy.org` | `Authorization: Bearer <key>` | Current, additive within v2 |
| Self-hosted ComfyUI | `http://127.0.0.1:8188` | none | the app's default |

Sources:
- v1: <https://docs.comfy.org/development/cloud/overview.md> — "All v1 requests require an
  API key passed in the `X-API-Key` header"; the page opens with a **Deprecated** banner
  pointing at v2.
- v2: <https://docs.comfy.org/api-reference/v2/overview.md> — base-URL table gives
  `https://cloud.comfy.org` with `Authorization: Bearer <api-key>`.
- Machine-readable: <https://docs.comfy.org/openapi-cloud.yaml> (`servers: https://cloud.comfy.org`,
  `securitySchemes.ApiKeyAuth: {type: apiKey, in: header, name: X-API-Key}`) and
  <https://docs.comfy.org/openapi-v2.yaml> (`securitySchemes.bearerAuth: {scheme: bearer}`).

So `X-API-Key` is right for v1 and wrong for v2. Our backend's env contract
(`COMFY_API_KEY` → `X-API-Key`) matches v1 only.

## 2. Measured: the unauthenticated shape of the host

`curl https://cloud.comfy.org<path>` on 2026-10-30, no key:

```
/                     -> 200 text/html      (the Comfy web app)
/api/system_stats     -> 200 application/json
/api/object_info      -> 401 {"code":"UNAUTHORIZED","message":"authentication required"}
/api/prompt           -> 401
/api/job/x/status     -> 401
/api/user             -> 401
/api/v2/jobs          -> 401
/object_info          -> 200 text/html      24415 bytes
/prompt               -> 200 text/html      24415 bytes
/history              -> 200 text/html      24415 bytes
```

**The last three are the trap.** They are the SPA shell, not the API. A client written
against local ComfyUI (`/prompt`, `/history/{id}`, `/object_info`) pointed at
`https://cloud.comfy.org` gets `200 OK` and HTML, so it fails later with a JSON decode error
that looks like a server bug. Verified body prefix: `<!doctype html><html lang="en">`.

`/api/system_stats` is public and announces the service identity — a clean reachability probe
for `GET /api/health` without spending a credit:

```json
{"devices":[],"system":{"argv":[],"cloud_version":"v0.515.0","comfyui_version":"v0.43.2",
 "deploy_environment":"cloud","embedded_python":false,"os":"","python_version":"",
 "pytorch_version":"","ram_free":0,"ram_total":0,"workflow_templates_version":"0.7.64"}}
```

## 3. Does it take the same API-format workflow JSON?

**Yes — the payload is identical.** v1 `POST /api/prompt` takes
`{"prompt": {<node_id>: {"class_type": ..., "inputs": {...}}}, ...}`, i.e. the same object
`graphs.py` already builds. From `openapi-cloud.yaml`, `PromptRequest`:

> `prompt` — type: object, required, "The workflow graph to execute"; `number` and `front`
> are "Accepted for ComfyUI API compatibility but **ignored** in cloud".

Response is the local shape too: `PromptResponse.prompt_id` (uuid) plus optional
`number` / `node_errors`. So submission needs only the `/api` prefix — no reshaping.

Sources: <https://docs.comfy.org/development/cloud/api-reference.md#submit-workflow> (curl
example posts `{"prompt": <workflow_api.json>}`), and `PromptRequest` in
<https://docs.comfy.org/openapi-cloud.yaml>.

v2 is different: `POST /api/v2/jobs` (see `openapi-v2.yaml`) with a bearer token. Not used here.

## 4. Job status — three different enums

This is the one place the docs contradict themselves, so poll on a *terminal set*, never on
a single success spelling:

| Source | Values |
|---|---|
| Cloud API Reference (v1 prose) | `queued_waiting`, `executing`, `success`, `error`, `non_retryable_error`, `lost`, `cancelled` |
| `openapi-cloud.yaml` `JobStatusResponse` | `waiting_to_dispatch`, `pending`, `in_progress`, `completed`, `error`, `cancelled` |
| `openapi-cloud.yaml` `JobDetailResponse` (`/api/jobs/{id}`) | `pending`, `in_progress`, `completed`, `failed`, `cancelled` |

Practical rule for `jobs.py`: treat `{success, completed}` as done; `{error, failed,
non_retryable_error, lost, cancelled}` as failed; everything else = still running.
Endpoints:

- `GET /api/job/{prompt_id}/status` → `{id, status, created_at, updated_at, error_message?}`
  (the v1 reference's own example; "the `prompt_id` returned by `/api/prompt` is the same
  value as `job_id`" — `openapi-cloud.yaml`, `/api/history_v2/{prompt_id}` description).
- `GET /api/jobs/{job_id}` → full detail **including `outputs` and `execution_error`**, but
  "`outputs` … only for terminal states". This is the cheapest way to get results: one call,
  no WebSocket.
- `GET /api/history_v2/{prompt_id}` (deprecated) → `{prompt_id: {prompt, outputs, status, meta}}`.
  **Its outer shape is identical to local `/history/{prompt_id}`**, so
  `ComfyClient.entry_from_history()` works unchanged once the path is right.

## 5. Fetching outputs — a `302`, and a credential hazard

`GET /api/view?filename=&subfolder=&type=output` returns **`302` to a GCS signed URL**, not
bytes (`openapi-cloud.yaml`, `/api/view` responses: `302` "Redirect to GCS signed URL";
`200` only when `channel=rgb|alpha` is passed for PNGs). Two consequences:

1. **Do not let the HTTP client follow that redirect.** The v1 reference says it in so many
   words: *"Step 1: read the 302 target. Do not use `-L` here: following the redirect would
   resend your API key to the storage host."* `server/cinema/comfy.py` builds its client with
   `follow_redirects=True` globally — against cloud that forwards `X-API-Key` to GCS.
   `fetch_view()` must request with redirects disabled, then fetch `Location` with no headers.
2. **Signed URLs last ~6 hours** and are not part of the contract ("treat it as an order of
   magnitude"). `url_expires_at` on a job output is *the job's* expiry, not the URL's. We
   already copy bytes into `CINEMA_DATA_DIR`, which is exactly the recommended fix
   (<https://docs.comfy.org/api-reference/v2/overview.md#showing-an-output-in-your-own-product>).

`subfolder` and `type` are **accepted and ignored** in cloud — storage is flat and
content-addressed. Harmless for us (we pass `subfolder=""`).

## 6. Exact differences to adapt (for `server/cinema/comfy.py`)

| what the client does now | cloud v1 | change |
|---|---|---|
| `POST /prompt` | `POST /api/prompt` | prefix |
| `GET /history/{id}` | `GET /api/history_v2/{id}` or `GET /api/jobs/{id}` | prefix **and** path |
| `GET /object_info` | `GET /api/object_info` | prefix |
| `POST /upload/image` | `POST /api/upload/image` | prefix; `subfolder` ignored |
| `GET /view` | `GET /api/view` | prefix; **302 handling** |
| `POST /queue {"delete":[id]}` | `POST /api/queue {"delete":[id]}` | prefix only — schema matches (`QueueManageRequest.delete` = "PENDING job IDs to cancel") |
| `Authorization` | `X-API-Key` | already correct for v1 |
| TLS | cloud cert is public | leave `COMFY_CA_BUNDLE` unset for cloud |

Already compatible, no change needed: the upload multipart field is `image` (cloud's example
posts `-F "image=@..."`); the queue tuple is `[job_number, prompt_id, workflow, output_node_ids,
metadata]` on both sides, so `queue_state()` still reads `[1]`; `POST /queue {"delete":[id]}`
has the same body as `QueueManageRequest`.

One unverified field: our submit payload sends `client_id`, which local ComfyUI wants and the
cloud `PromptRequest` schema does not list (it also does not forbid extra keys). Harmless if
ignored; unknown if rejected. Try it, and drop it for cloud if a `400` names it.

Minimal, reversible implementation (no behaviour change for local):

```python
# in ComfyClient.__init__
self.cloud = os.environ.get("COMFY_SURFACE", "auto") == "cloud" or (
    os.environ.get("COMFY_SURFACE", "auto") == "auto"
    and self.api_key and "comfy.org" in self.base_url)
self.prefix = "/api" if self.cloud else ""
```

then `self._url(self.prefix + "/prompt")` etc., `history()` → `self.prefix + "/history_v2/%s"`,
and `fetch_view()` → `follow_redirects=False` + a bare `httpx.get(location)` when `self.cloud`.
`COMFY_SURFACE=local|cloud|auto` (default `auto`) keeps the self-hosted path byte-identical.

**Unverified:** whether open-source ComfyUI also answers the `/api/`-prefixed aliases (its
documented route table lists only the unprefixed forms —
<https://docs.comfy.org/development/comfyui-server/comms_routes.md>). No local server was
running when this was written, so it could not be measured. If it does, the prefix could be
unconditional; do not assume it without a probe.

## 7. Custom node packs — the blocker

> "Comfy Cloud runs a fixed set of custom nodes that Comfy maintains. **You cannot install
> your own node packs there**, so a workflow that depends on a pack Cloud does not include
> will not run as it is."
> — <https://docs.comfy.org/get_started/cloud.md#custom-nodes-on-comfy-cloud>

So `comfy_nodes/ComfyUI-CinemaRig` (`CinemaCameraRig`, `CinemaFilmGrade`) **cannot run on
Comfy Cloud**, ever, by design. Unsupported nodes are flagged on the canvas and under
**Unsupported Node Packs** in the Errors tab. Any cloud run of this app must use graphs built
without those two nodes — which is fine, because the same table lives in
`server/cinema/camera.py` and the grade is a local ffmpeg pass, but it must be a deliberate
`graphs.py` branch, not an assumption.

Models: cloud has "all models … pre-installed" and its own list — mostly Comfy core models
plus partner nodes. **Whether the exact filenames in `CINEMA_MODELS`
(`wan2.2_i2v_*_14B_fp8_scaled.safetensors`, `RealVisXL_V4.0.safetensors`) exist there is
unknown**: `GET /api/object_info` is authenticated and we hold no key. Do not promise it.
Check with `curl -H "X-API-Key: $KEY" https://cloud.comfy.org/api/object_info` before
claiming a cloud demo works.

## 8. Honest limits (all from primary docs)

- **Paid subscription required.** "API access requires a paid Comfy Cloud subscription; the
  Free tier does not include it." (<https://docs.comfy.org/development/cloud/overview.md>)
- **Credits, not a flat rate.** API jobs draw from the same monthly credit pool as the web
  UI; no separate API pool. Top-ups cost money.
- **Concurrency: 1 / 3 / 5** concurrent workflows on Standard / Creator / Pro; extra jobs
  queue, max 100 queued.
- **Runtime caps: 30 min (Standard, Creator), 1 h (Pro)** — a job past it is cancelled. Our
  `final` preset (81 frames, 20 steps) on a 14B two-expert Wan graph is not obviously under
  that on shared hardware.
- **`402` = insufficient credits, `429` = subscription inactive** — both look like generic
  failures unless the client maps them.
- **Retention is a placeholder.** Job `expires_at` is `created_at + 30 days` and the docs say
  plainly it is "not a commitment about how long an output stays fetchable". Re-host bytes.
- **v1 is deprecated** and may change without notice; v2 is the forward path and uses a
  different header (`Bearer`), a different job path (`/api/v2/jobs`) and signed asset URLs.
  If we adapt, adapt for v1 today, v2 next.
- **Output URLs are not shareable.** `…/api/v2/assets/{id}/content` answers `401` without the
  key; only the ~6 h signed URL is public.

## 9. What this means for the submission

The app talks to **self-hosted ComfyUI over its core HTTP API** (`/prompt`, `/history`,
`/view`, `/object_info`, `/upload/image`). It does **not** speak the hosted "Comfy API"
(v1 or v2) without the adapter above, and it cannot use its own node pack on Comfy Cloud at
all. Therefore, on the Typeform's "Which parts of ComfyUI did you use?", **"Comfy API" is not
claimed** — see `SUBMISSION.md`. Claim it the day the `COMFY_SURFACE=cloud` branch is merged
and a cloud run is on camera.

## 10. Reproduce

```bash
curl -s https://cloud.comfy.org/api/system_stats | head -c 200
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' https://cloud.comfy.org/api/object_info
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' https://cloud.comfy.org/prompt   # 200 text/html
curl -sL https://docs.comfy.org/openapi-cloud.yaml | grep -n '^  /'
```
