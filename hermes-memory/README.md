# cognee-hermes-memory — mount-delivered Hermes memory provider

This is a second SDK in the cognee-sdk repo. It delivers a **Hermes
memory-provider plugin** (`cognee-local`) into a Hermes workshop by **mounting**
it into `~/.hermes/plugins/` — there is no `cp` into Hermes' config dir, and the
provider updates in place whenever this SDK is refreshed.

The provider speaks the **self-hosted cognee REST server**'s own endpoints
(`/api/v1/add`, `/api/v1/cognify`, `/api/v1/search`) over plain HTTP using only
the Python standard library — no cloud, and no API key when the cognee server
runs with access control disabled. It pairs with the **cognee SDK's REST server**
running in another workshop (or the same one).

---

## How the pieces connect

Two Workshop interfaces, two SDKs you already have:

| Plane | Provider | Consumer | Interface |
|---|---|---|---|
| **Code** (the provider) | `cognee-hermes-memory : cognee-hermes-plugin` (mount slot) | `hermes-agent : <plugins mount plug>` → `~/.hermes/plugins/cognee_local` | `mount` |
| **Data** (the API) | `cognee : cognee-api` (tunnel slot, :8000) | `hermes-agent : memory-backend` (tunnel plug, :8000) | `tunnel` |

The data path needs **no new plug** — the Hermes SDK already ships a
`memory-backend` tunnel plug defaulting to port 8000, which is exactly the
cognee REST port.

The code path needs **one plug added to the Hermes SDK** (see below). Hermes
already nests a submount inside `~/.hermes` (its `hermes-secrets` plug), so a
plugin submount at `~/.hermes/plugins/cognee_local` follows an established
pattern.

### Required Hermes SDK addition

Add to `hermes-agent-sdk/sdkcraft.yaml` under `plugs:`

```yaml
  # External memory-provider plugin, delivered by mount (e.g. cognee-local from
  # the cognee-hermes-memory SDK). Mounts a single plugin package into the
  # Hermes plugin path; the agent imports it from ~/.hermes/plugins/cognee_local.
  cognee-plugin:
    interface: mount
    workshop-target: /home/workshop/.hermes/plugins/cognee_local
    mode: 0o755
```

---

## Example combined workshop

```yaml
# workshop.yaml — Hermes + cognee + the bridge, wired by mount + tunnel
name: hermes-cognee
base: ubuntu@24.04

sdks:
  - name: cognee
    channel: latest/stable
  - name: cognee-hermes-memory
    channel: latest/stable
  - name: hermes-agent
    channel: latest/stable

connections:
  # Code: mount the provider into Hermes' plugin path (no copy).
  - slot: cognee-hermes-memory:cognee-hermes-plugin
    plug: hermes-agent:cognee-plugin
  # Data: Hermes' memory backend tunnel -> cognee's REST API.
  - slot: cognee:cognee-api
    plug: hermes-agent:memory-backend
```

Then, one-time, inside the Hermes workshop:

```bash
workshop shell
# Select the provider:
#   ~/.hermes/config.yaml ->  memory: { memory_enabled: true, provider: cognee-local }
systemctl --user restart hermes-gateway
```

And enable the cognee REST server in the cognee workshop (`COGNEE_SERVE=1` in
`~/.cognee/.env`, plus an `LLM_API_KEY`), keyless for the provider by disabling
access control:

```bash
# ~/.cognee/.env
COGNEE_SERVE=1
ENABLE_BACKEND_ACCESS_CONTROL=false
REQUIRE_AUTHENTICATION=false
LLM_API_KEY=sk-...
```

If you instead leave access control on, give the provider an account via
`COGNEE_EMAIL` / `COGNEE_PASSWORD` (or `~/.hermes/cognee_local.json`) and it will
log in for a Bearer token automatically.

---

## Config reference

| Key | Env var | Default | Purpose |
|---|---|---|---|
| `base_url` | `COGNEE_BASE_URL` | `http://localhost:8000` | cognee server URL (the tunnel) |
| `dataset` | `COGNEE_DATASET` | `hermes` | dataset to read/write |
| `search_type` | `COGNEE_SEARCH_TYPE` | `GRAPH_COMPLETION` | cognee `searchType` enum |
| `top_k` | `COGNEE_TOP_K` | `10` | max results requested |
| `email` / `password` | `COGNEE_EMAIL` / `COGNEE_PASSWORD` | _(unset)_ | only if the server enforces auth |

## Slots (resources this SDK provides)

### `cognee-hermes-plugin`

- Interface: `mount`
- Workshop source: `/home/workshop/.local/share/cognee-hermes/plugins/cognee_local`
- Purpose: shares the `cognee_local` provider package so a connected Hermes SDK
  plug mounts it into `~/.hermes/plugins/cognee_local`.

This SDK consumes no plugs.

## Limitations / things to verify

- The `/api/v1` request/response shapes in the provider are derived from the
  cognee API reference, not a live run. Verify with `sdkcraft try` + the cognee
  SDK (`COGNEE_SERVE=1`) before production use.
- cognee splits ingestion into **add + cognify**; cognify rebuilds the graph and
  is LLM-expensive, so writes run off-thread with `runInBackground=true`. Tune
  the cadence in `sync_turn` if every-turn cognify is too costly.
