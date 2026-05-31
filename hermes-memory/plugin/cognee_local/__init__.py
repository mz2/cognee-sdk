"""Hermes memory provider: cognee-local.

A standalone Hermes memory-provider plugin (the sanctioned ~/.hermes/plugins/
extension path) that points Hermes at a *self-hosted* cognee REST server instead
of any cloud service. This is the bridge that lets a Hermes workshop use the
cognee SDK's REST server (the `cognee-api` tunnel) as its memory backend.

The provider speaks the self-hosted server's own endpoints over plain HTTP using
only the Python standard library:

  - write : POST /api/v1/add        (multipart; text goes in as a file part)
            POST /api/v1/cognify    (build/refresh the knowledge graph)
  - read  : POST /api/v1/search     (GRAPH_COMPLETION by default)

If the cognee server runs with access control disabled
(ENABLE_BACKEND_ACCESS_CONTROL=false and REQUIRE_AUTHENTICATION=false) the
provider needs no credentials. Otherwise set COGNEE_EMAIL / COGNEE_PASSWORD and
it will obtain a Bearer token via POST /api/v1/auth/login and retry on 401.

Config via environment variables (or $HERMES_HOME/cognee_local.json):
  COGNEE_BASE_URL   — base URL of the cognee server (default: http://localhost:8000)
  COGNEE_DATASET    — dataset name to read/write (default: hermes)
  COGNEE_SEARCH_TYPE— cognee searchType enum (default: GRAPH_COMPLETION)
  COGNEE_TOP_K      — max results to request (default: 10)
  COGNEE_EMAIL      — optional account email (only if the server enforces auth)
  COGNEE_PASSWORD   — optional account password

Wiring: connect the Hermes workshop's `memory-backend` tunnel plug to the cognee
workshop's `cognee-api` slot so that http://localhost:8000 inside the Hermes
workshop reaches the cognee server.

NOTE: the cognee `/api/v1` request/response shapes encoded below are derived
from the cognee API reference (docs.cognee.ai/api-reference), not from a live
run. Verify against a running server (`COGNEE_SERVE=1` + the cognee SDK) before
relying on this in production.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

_TIMEOUT = 30.0  # cognify can be slow; give writes room (they run off-thread)


def _load_config() -> dict:
    from hermes_constants import get_hermes_home

    config = {
        "base_url": os.environ.get("COGNEE_BASE_URL", "http://localhost:8000"),
        "dataset": os.environ.get("COGNEE_DATASET", "hermes"),
        "search_type": os.environ.get("COGNEE_SEARCH_TYPE", "GRAPH_COMPLETION"),
        "top_k": int(os.environ.get("COGNEE_TOP_K", "10")),
        "email": os.environ.get("COGNEE_EMAIL", ""),
        "password": os.environ.get("COGNEE_PASSWORD", ""),
    }
    config_path = get_hermes_home() / "cognee_local.json"
    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({k: v for k, v in file_cfg.items() if v not in (None, "")})
        except Exception:
            pass
    return config


def _encode_multipart(fields: Dict[str, str], file_field: str, text: str) -> tuple[bytes, str]:
    """Encode a single text payload as a multipart/form-data body (stdlib only).

    cognee's /api/v1/add takes `data` as an array of binary uploads, so a piece
    of conversational text is sent as one in-memory "file" part.
    """
    boundary = f"----cognee{uuid.uuid4().hex}"
    crlf = "\r\n"
    parts: List[str] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}{crlf}")
        parts.append(f'Content-Disposition: form-data; name="{name}"{crlf}{crlf}')
        parts.append(f"{value}{crlf}")
    parts.append(f"--{boundary}{crlf}")
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="memory.txt"{crlf}'
    )
    parts.append(f"Content-Type: text/plain{crlf}{crlf}")
    parts.append(f"{text}{crlf}")
    parts.append(f"--{boundary}--{crlf}")
    body = "".join(parts).encode("utf-8")
    return body, boundary


class _CogneeClient:
    """Minimal HTTP client for the self-hosted cognee server (stdlib only)."""

    def __init__(self, base_url: str, email: str = "", password: str = ""):
        self._base = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._token: str | None = None

    # -- auth ----------------------------------------------------------------
    def _login(self) -> None:
        if not (self._email and self._password):
            return
        data = urllib.parse.urlencode(
            {"username": self._email, "password": self._password}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base}/api/v1/auth/login", data=data, method="POST"
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self._token = payload.get("access_token")

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    # -- transport -----------------------------------------------------------
    def _send(self, req: urllib.request.Request) -> Any:
        for hk, hv in self._auth_headers().items():
            req.add_header(hk, hv)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            # Access control enabled but we have creds: log in once and retry.
            if exc.code in (401, 403) and (self._email and self._password):
                self._login()
                fresh = urllib.request.Request(
                    req.full_url, data=req.data, method=req.get_method()
                )
                for hk, hv in (req.headers | self._auth_headers()).items():
                    fresh.add_header(hk, hv)
                with urllib.request.urlopen(fresh, timeout=_TIMEOUT) as resp:
                    raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
            raise

    def _post_json(self, path: str, body: dict) -> Any:
        req = urllib.request.Request(
            f"{self._base}{path}",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        return self._send(req)

    # -- cognee operations ---------------------------------------------------
    def add(self, text: str, dataset: str) -> Any:
        body, boundary = _encode_multipart({"datasetName": dataset}, "data", text)
        req = urllib.request.Request(
            f"{self._base}/api/v1/add", data=body, method="POST"
        )
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        return self._send(req)

    def cognify(self, dataset: str, *, background: bool = True) -> Any:
        return self._post_json(
            "/api/v1/cognify", {"datasets": [dataset], "runInBackground": background}
        )

    def search(self, query: str, dataset: str, search_type: str, top_k: int) -> Any:
        return self._post_json(
            "/api/v1/search",
            {
                "query": query,
                "searchType": search_type,
                "datasets": [dataset],
                "topK": top_k,
            },
        )


SEARCH_SCHEMA = {
    "name": "cognee_search",
    "description": "Search the cognee knowledge graph by meaning. Returns relevant context.",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to search for."}},
        "required": ["query"],
    },
}

REMEMBER_SCHEMA = {
    "name": "cognee_remember",
    "description": "Store a durable fact in cognee (an explicit preference, decision, or detail).",
    "parameters": {
        "type": "object",
        "properties": {"fact": {"type": "string", "description": "The fact to store."}},
        "required": ["fact"],
    },
}


def _unwrap(response: Any) -> List[str]:
    """cognee /search returns a list of {search_result, dataset_id, dataset_name}."""
    items = response if isinstance(response, list) else response.get("results", [])
    out: List[str] = []
    for r in items:
        val = r.get("search_result") if isinstance(r, dict) else r
        if val in (None, ""):
            continue
        out.append(val if isinstance(val, str) else json.dumps(val))
    return out


class CogneeLocalMemoryProvider(MemoryProvider):
    """cognee memory backed by a self-hosted cognee REST server."""

    def __init__(self):
        self._config: dict = {}
        self._client: _CogneeClient | None = None
        self._dataset = "hermes"
        self._search_type = "GRAPH_COMPLETION"
        self._top_k = 10
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: threading.Thread | None = None
        self._sync_thread: threading.Thread | None = None

    @property
    def name(self) -> str:
        return "cognee-local"

    def is_available(self) -> bool:
        return True

    def get_config_schema(self):
        return [
            {"key": "base_url", "description": "Self-hosted cognee server URL", "default": "http://localhost:8000", "env_var": "COGNEE_BASE_URL"},
            {"key": "dataset", "description": "Dataset name to read/write", "default": "hermes", "env_var": "COGNEE_DATASET"},
            {"key": "search_type", "description": "cognee searchType enum", "default": "GRAPH_COMPLETION", "env_var": "COGNEE_SEARCH_TYPE"},
        ]

    def save_config(self, values, hermes_home):
        from pathlib import Path

        path = Path(hermes_home) / "cognee_local.json"
        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except Exception:
                pass
        existing.update(values)
        path.write_text(json.dumps(existing, indent=2))

    def initialize(self, session_id: str, **kwargs) -> None:
        self._config = _load_config()
        self._client = _CogneeClient(
            self._config["base_url"], self._config.get("email", ""), self._config.get("password", "")
        )
        self._dataset = self._config.get("dataset", "hermes")
        self._search_type = self._config.get("search_type", "GRAPH_COMPLETION")
        self._top_k = int(self._config.get("top_k", 10))
        try:
            self._client._login()
        except Exception as e:
            logger.debug("cognee-local: login skipped/failed: %s", e)

    def system_prompt_block(self) -> str:
        return (
            "# Cognee Memory (self-hosted)\n"
            f"Active. Dataset: {self._dataset}.\n"
            "Use cognee_search to recall context, cognee_remember to store facts."
        )

    # -- read path -----------------------------------------------------------
    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=5.0)
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        return f"## Cognee Memory\n{result}" if result else ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        def _run():
            try:
                lines = _unwrap(self._client.search(query, self._dataset, self._search_type, self._top_k))
                if lines:
                    with self._prefetch_lock:
                        self._prefetch_result = "\n".join(f"- {l}" for l in lines)
            except Exception as e:
                logger.debug("cognee-local prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(target=_run, daemon=True, name="cognee-local-prefetch")
        self._prefetch_thread.start()

    # -- write path ----------------------------------------------------------
    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        # add() is cheap; cognify() rebuilds the graph and is expensive, so it
        # runs in the background. Tune cadence here if every-turn cognify is too
        # heavy for your LLM budget.
        def _sync():
            try:
                text = f"User: {user_content}\nAssistant: {assistant_content}"
                self._client.add(text, self._dataset)
                self._client.cognify(self._dataset, background=True)
            except Exception as e:
                logger.warning("cognee-local sync failed: %s", e)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)
        self._sync_thread = threading.Thread(target=_sync, daemon=True, name="cognee-local-sync")
        self._sync_thread.start()

    # -- tools ---------------------------------------------------------------
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA, REMEMBER_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if self._client is None:
            return tool_error("cognee-local not initialized")

        if tool_name == "cognee_search":
            query = args.get("query", "")
            if not query:
                return tool_error("Missing required parameter: query")
            try:
                lines = _unwrap(self._client.search(query, self._dataset, self._search_type, self._top_k))
                if not lines:
                    return json.dumps({"result": "No relevant memories found."})
                return json.dumps({"results": lines, "count": len(lines)})
            except Exception as e:
                return tool_error(f"Search failed: {e}")

        if tool_name == "cognee_remember":
            fact = args.get("fact", "")
            if not fact:
                return tool_error("Missing required parameter: fact")
            try:
                self._client.add(fact, self._dataset)
                self._client.cognify(self._dataset, background=True)
                return json.dumps({"result": "Fact stored (graph update queued)."})
            except Exception as e:
                return tool_error(f"Failed to store: {e}")

        return tool_error(f"Unknown tool: {tool_name}")

    def shutdown(self) -> None:
        for t in (self._prefetch_thread, self._sync_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        self._client = None


def register(ctx) -> None:
    """Register cognee-local as a memory provider plugin."""
    ctx.register_memory_provider(CogneeLocalMemoryProvider())
