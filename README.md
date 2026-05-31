# cognee SDK for Workshop

This SDK provides [cognee](https://github.com/topoteretes/cognee), an
open-source AI memory engine that turns your documents and conversations into a
queryable knowledge graph, inside a workshop in two interchangeable forms that
share **one local store**: an **in-process library** (`import cognee`) and a
**REST API server** (cognee's own FastAPI app, run fully embedded and exposed on
a tunnel). The relational (SQLite), vector (LanceDB), and graph (Kuzu) stores
are all file-based and run locally under `~/.cognee`, which is persisted on the
host across workshop updates along with the virtual environment.

---

## Reference workshop

A minimal workshop:

```yaml
# workshop.yaml
name: cognee
base: ubuntu@24.04
sdks:
  - name: cognee
    channel: latest/stable

actions:
  shell: python
```

This makes `import cognee` available in the workshop's Python. To also run the
REST server, set `COGNEE_SERVE=1` in `~/.cognee/.env` (see below) and connect
the `cognee-api` tunnel from the host or a peer workshop.

---

## Using the SDK

### Prerequisites, project layout

1. No prerequisite SDKs are required. For a fully local (offline) backend, run
   an [Ollama](https://ollama.com) inside the workshop (add the `gpu` plug) and
   point `LLM_PROVIDER` / `EMBEDDING_PROVIDER` at it in `~/.cognee/.env`.
2. No specific project layout is needed.
3. On launch the SDK installs `cognee` into a persisted virtual environment,
   pins the relational/vector/graph stores onto `~/.cognee` via
   `SYSTEM_ROOT_DIRECTORY` / `DATA_ROOT_DIRECTORY`, seeds `~/.cognee/.env`, and
   installs the REST server systemd unit (started only when `COGNEE_SERVE=1`).

### Backend credentials

cognee needs an LLM and an embedder. By default both are OpenAI — add your key
to `~/.cognee/.env`:

```bash
workshop shell
echo 'LLM_API_KEY=sk-...' >> ~/.cognee/.env
exit
workshop refresh
```

To run fully offline, set the Ollama block in `~/.cognee/.env` (uncomment the
`LLM_PROVIDER=ollama` / `EMBEDDING_PROVIDER=ollama` lines). Note: if you
configure only the LLM or only the embedder, the other one falls back to OpenAI
— set both to stay local.

### In-process library

```bash
workshop shell
python - <<'PY'
import asyncio, cognee

async def main():
    # Low-level pipeline: ingest -> build the knowledge graph -> query it.
    await cognee.add("Cognee turns documents into AI memory.")
    await cognee.cognify()
    results = await cognee.search("What does cognee do?")
    print(results)

asyncio.run(main())
PY
```

Because `SYSTEM_ROOT_DIRECTORY` and `DATA_ROOT_DIRECTORY` point at `~/.cognee`,
everything you ingest persists there across workshop updates. Recent cognee
releases also expose a higher-level `await cognee.remember(...)` /
`await cognee.recall(...)` API over the same store.

### REST API server

```bash
workshop shell
sed -i 's/^COGNEE_SERVE=0/COGNEE_SERVE=1/' ~/.cognee/.env
systemctl --user restart cognee-server
curl -s http://localhost:8000/docs >/dev/null && echo "API up"
# OpenAPI docs at http://localhost:8000/docs
```

The server is cognee's own FastAPI app (`cognee.api.client:app`), running
against the same embedded `~/.cognee` store. Reach it from the host or a peer
workshop over the `cognee-api` tunnel.

> Kuzu (the default graph store) uses file-based locking and is not built for
> concurrent writers, so prefer using **either** the in-process library **or**
> the REST server against a given store at a time.

### Verify from the command line

```bash
workshop info     # health line: "cognee <version> — in-process library ready ..."
workshop shell
python -c "import importlib.metadata as m; print(m.version('cognee'))"
```

---

## Plugs (resources this SDK consumes)

### `cognee-data`

- Interface: `mount`
- Workshop target: `/home/workshop/.cognee`
- Mode: `0o700`
- Purpose: persists the embedded store — the relational (SQLite), vector
  (LanceDB), and graph (Kuzu) databases under `system/`, ingested data under
  `data/`, and `.env` (credentials + the `COGNEE_SERVE` toggle) — across
  workshop updates.

### `cognee-venv`

- Interface: `mount`
- Workshop target: `/home/workshop/.local/share/cognee`
- Purpose: persists the virtual environment so the `cognee` install happens once.

### `pip-cache`

- Interface: `mount`
- Workshop target: `/home/workshop/.cache/pip`
- Purpose: persists the pip download cache to speed up reinstalls.

### `gpu`

- Interface: `gpu`
- Purpose: GPU access for a local in-workshop embedding/LLM backend (e.g. an
  Ollama running inside the workshop). Ignored when cognee is backed by a hosted
  API such as OpenAI.

## Slots (resources this SDK provides)

### `cognee-api`

- Interface: `tunnel`
- Endpoint: `8000`
- Purpose: exposes the cognee REST API server (when `COGNEE_SERVE=1`) to the
  host or a peer workshop.

---

## Documentation and guidance

- [cognee official documentation](https://docs.cognee.ai/)
- [cognee REST API server guide](https://docs.cognee.ai/guides/deploy-rest-api-server)
- [Workshop documentation](https://ubuntu.com/workshop/docs/)

---

## Community and support

- cognee community:
  [GitHub](https://github.com/topoteretes/cognee) ·
  [Discord](https://discord.gg/NQPKmU5CCg)
- Workshop forum:
  [Discourse](https://discourse.ubuntu.com/)
- Please review our
  [Code of Conduct](https://ubuntu.com/community/ethos/code-of-conduct) before
  participating.

---

## Contributions

All contributions, including code, documentation updates, and issue reports,
are welcome!

- See `CONTRIBUTING.md` for guidelines.
- Open issues or pull requests on the official repository.

---

## License and copyright

Copyright 2026 Canonical Ltd.

This SDK is licensed under Apache-2.0.

cognee is licensed under the
[Apache-2.0 License](https://github.com/topoteretes/cognee/blob/main/LICENSE).
