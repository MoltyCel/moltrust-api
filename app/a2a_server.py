"""A2A JSON-RPC transport for MolTrust.

Mounts a standard A2A v1.0 ``message/send`` JSON-RPC endpoint at ``/a2a`` using
the official ``a2a-sdk``. A2A registries (e.g. a2aregistry.org) probe an agent's
declared transport with the A2A SDK client; without a real JSON-RPC endpoint the
card is hard-rejected (``NO_TRANSPORTS``) or flagged non-conformant (``404``).

MVP scope: ``message/send`` returns a capability description of the five MolTrust
skills. That is sufficient for the registry conformance probe ("WORKING").
Per-skill routing (trust-score, did-resolution, …) is a follow-up.

Coupled change (Layer A, repo ``moltrust-web``): the served
``/.well-known/agent-card.json`` must set
``supportedInterfaces[0].protocolBinding`` to ``"JSONRPC"`` and ``url`` to
``https://api.moltrust.ch/a2a``, then be re-signed via the JWS pipeline.

All ``a2a`` imports are deferred into :func:`mount_a2a` so importing this module
never requires the SDK; a missing/broken dependency degrades to "no /a2a route"
instead of taking down the whole API.
"""

import logging
import uuid

logger = logging.getLogger(__name__)

# Path the JSON-RPC endpoint is mounted at; must match the agent card's
# supportedInterfaces[0].url path component.
A2A_RPC_PATH = "/a2a"
_AGENT_URL = "https://api.moltrust.ch/a2a"

# (skill id, human name) — mirrors the five skills declared in the agent card.
_SKILLS = [
    ("trust-score", "Agent Trust Score"),
    ("did-resolution", "DID Resolution"),
    ("credential-verification", "Verifiable Credential Verification"),
    ("wallet-binding", "Wallet Binding Verification"),
    ("sybil-detection", "Sybil & Anomaly Detection"),
]

_CAPABILITY_TEXT = (
    "MolTrust Trust Registry — trust infrastructure for autonomous AI agents. "
    "Skills: " + ", ".join(name for _, name in _SKILLS) + ". "
    "Ask me to score an agent's trust, resolve a W3C DID, verify a credential, "
    "check a DID-to-wallet binding, or run sybil/anomaly detection. "
    "Paid skill calls are metered via the REST API with an X-API-Key."
)


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
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
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
        from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
        from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
        from a2a.server.tasks import InMemoryTaskStore

        class _CapabilityExecutor(AgentExecutor):
            """Immediate-response executor: enqueues one capability Message."""

            async def execute(self, context, event_queue):
                from a2a.types import Message, Part, Role

                await event_queue.enqueue_event(
                    Message(
                        message_id=str(uuid.uuid4()),
                        role=Role.ROLE_AGENT,
                        parts=[Part(text=_CAPABILITY_TEXT)],
                    )
                )

            async def cancel(self, context, event_queue):
                return None

        card = _build_card()
        handler = DefaultRequestHandler(
            agent_executor=_CapabilityExecutor(),
            task_store=InMemoryTaskStore(),
            agent_card=card,
        )
        add_a2a_routes_to_fastapi(
            app,
            jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=A2A_RPC_PATH),
        )
        logger.info("A2A JSON-RPC endpoint mounted at %s", A2A_RPC_PATH)
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "A2A endpoint not mounted (%s): %s", type(exc).__name__, exc
        )
        return False
