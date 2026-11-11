"""A2A JSON-RPC transport for MolTrust.

Mounts a standard A2A v1.0 JSON-RPC endpoint at ``/a2a`` using the official
``a2a-sdk``. A2A registries (e.g. a2aregistry.org) probe an agent's declared
transport with the A2A SDK client; without a real JSON-RPC endpoint the card is
hard-rejected (``NO_TRANSPORTS``) or flagged non-conformant (``404``).

Role (ops job A2A, decision of 21.10.2026): the endpoint declares capabilities
and executes no tasks. Every skill in the agent card runs over the REST API, and
the card names the route for each. A task message (``SendMessage``, streaming
included) is refused with ``UnsupportedOperationError`` (-32004), whose message
gives the reason and the way over REST. Until then ``message/send`` answered
every message with the same success text, whatever it asked for.

Executing tasks over A2A is a separate job, taken up once a caller uses it.

All ``a2a`` imports are deferred into :func:`mount_a2a` so importing this module
never requires the SDK; a missing/broken dependency degrades to "no /a2a route"
instead of taking down the whole API.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

# Path the JSON-RPC endpoint is mounted at; must match the agent card's
# supportedInterfaces[0].url path component.
A2A_RPC_PATH = "/a2a"
_AGENT_URL = "https://api.moltrust.ch/a2a"

# Paths A2A clients post to without reading the card first: ``/jsonrpc`` with a
# JSON-RPC envelope, ``/v1/message:send`` with the REST binding's body. Each
# had ~150 POSTs in 30 days against a 404 (auto_repair digest, 16.10.2026).
# Both reach the same handler as /a2a, so the answer is the same; the card
# still names /a2a only.
A2A_JSONRPC_ALIASES = ("/jsonrpc",)
A2A_REST_PREFIX = "/v1"
A2A_REST_SEND_PATH = A2A_REST_PREFIX + "/message:send"

_AGENT_CARD_URL = "https://api.moltrust.ch/.well-known/agent-card.json"
_REST_OPENAPI_URL = "https://api.moltrust.ch/openapi.json"
_REST_DOCS_URL = "https://api.moltrust.ch/docs"

# The signed public card in this repo; the skill list below is read from it so
# the ARD manifest and the in-memory card cannot drift from what is published.
_CARD_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".well-known", "agent-card.json",
)


def _load_skills() -> list[tuple[str, str]]:
    try:
        with open(_CARD_PATH, encoding="utf-8") as f:
            return [(s["id"], s["name"]) for s in json.load(f)["skills"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("agent card skills not readable (%s): %s", type(exc).__name__, exc)
        return []


# (skill id, human name) — the skills declared in the agent card.
_SKILLS = _load_skills()

# Why a task message is refused, and where to go instead. Read by callers, so
# it says what to do rather than only what went wrong.
TASKS_NOT_SUPPORTED = (
    "This agent does not execute A2A tasks. Its skills run over the MolTrust "
    "REST API, and the agent card names the route for each skill "
    f"({_AGENT_CARD_URL}). REST reference: {_REST_OPENAPI_URL}"
)
# google.rpc.ErrorInfo metadata is a string map.
TASKS_NOT_SUPPORTED_DATA = {
    "cause": "a2a-tasks-not-supported",
    "agentCard": _AGENT_CARD_URL,
    "restApi": _REST_OPENAPI_URL,
    "restDocs": _REST_DOCS_URL,
}


def _build_card():
    """Build the in-memory AgentCard the request handler validates against.

    Not served to clients — the monolith serves the canonical (signed) card from
    the static ``/.well-known/agent-card.json`` file. This mirror only needs the
    transport + skills the handler reasons about.
    """
    from a2a.types import (
        AgentCapabilities,
        AgentCard,
        AgentInterface,
        AgentSkill,
    )

    return AgentCard(
        name="MolTrust Trust Registry",
        description="Production trust infrastructure for autonomous AI agents.",
        version="1.0.1",
        supported_interfaces=[
            AgentInterface(
                url=_AGENT_URL,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(id=skill_id, name=name, description=name, tags=["trust"])
            for skill_id, name in _SKILLS
        ],
    )


def mount_a2a(app) -> bool:
    """Mount the A2A JSON-RPC routes on the FastAPI ``app``.

    Best-effort: any failure (missing ``a2a-sdk``, SDK API change) is logged and
    swallowed so the rest of the API keeps serving. Returns ``True`` iff mounted.
    """
    try:
        from a2a.client import ClientConfig  # noqa: F401  (import smoke)
        from a2a.server.agent_execution import AgentExecutor
        from a2a.server.request_handlers import DefaultRequestHandler
        from a2a.server.request_handlers.request_handler import validate_request_params
        from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
        from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
        from a2a.server.routes.rest_routes import create_rest_routes
        from a2a.server.tasks import InMemoryTaskStore

        from a2a.utils.errors import UnsupportedOperationError

        def _refuse():
            return UnsupportedOperationError(
                message=TASKS_NOT_SUPPORTED, data=dict(TASKS_NOT_SUPPORTED_DATA)
            )

        class _NoTaskExecutor(AgentExecutor):
            """Never reached: the handler refuses before it runs an executor."""

            async def execute(self, context, event_queue):
                raise _refuse()

            async def cancel(self, context, event_queue):
                raise _refuse()

        class _CapabilitiesOnlyHandler(DefaultRequestHandler):
            """Refuses every task message; reads (GetTask, …) stay with the SDK.

            A malformed message still gets the SDK's -32602 with the field at
            fault, so validation runs before the refusal."""

            @validate_request_params
            async def on_message_send(self, params, context):
                raise _refuse()

            @validate_request_params
            async def on_message_send_stream(self, params, context):
                raise _refuse()
                yield  # pragma: no cover - makes this an async generator

        card = _build_card()
        handler = _CapabilitiesOnlyHandler(
            agent_executor=_NoTaskExecutor(),
            task_store=InMemoryTaskStore(),
            agent_card=card,
        )
        jsonrpc_routes = [
            route
            for path in (A2A_RPC_PATH, *A2A_JSONRPC_ALIASES)
            for route in create_jsonrpc_routes(handler, rpc_url=path)
        ]
        # Only message:send from the REST binding. The SDK builds the whole
        # task surface plus a /{tenant} mount; none of that is declared here.
        rest_routes = [
            route
            for route in create_rest_routes(handler, path_prefix=A2A_REST_PREFIX)
            if getattr(route, "path", None) == A2A_REST_SEND_PATH
        ]
        if len(rest_routes) != 1:
            # An SDK that renamed the route must not cost /a2a its mount.
            logger.warning("A2A REST %s not mounted: SDK built %d matching routes",
                           A2A_REST_SEND_PATH, len(rest_routes))
            rest_routes = []
        add_a2a_routes_to_fastapi(
            app,
            jsonrpc_routes=jsonrpc_routes,
            rest_routes=rest_routes,
        )
        logger.info(
            "A2A endpoint mounted at %s (also %s, %s)",
            A2A_RPC_PATH, ", ".join(A2A_JSONRPC_ALIASES), A2A_REST_SEND_PATH,
        )
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "A2A endpoint not mounted (%s): %s", type(exc).__name__, exc
        )
        return False
