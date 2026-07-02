"""/.well-known/agent.json — backward-compat alias for the legacy A2A discovery
path. Serves the same plain public card as /.well-known/agent-card.json."""
import asyncio
import json
from unittest import mock

import app.main as main


def test_agent_json_alias_route_registered():
    paths = {getattr(r, "path", None) for r in main.app.routes}
    assert "/.well-known/agent.json" in paths


def test_agent_json_alias_serves_public_card():
    sample = {"name": "MolTrust Trust Registry", "protocolVersion": "0.3.0", "version": "1.0.1"}
    with mock.patch.object(main, "_load_public_agent_card", return_value=sample):
        resp = asyncio.run(main.well_known_agent_json_alias())
    assert resp.status_code == 200
    assert json.loads(resp.body) == sample
    assert resp.headers.get("cache-control") == "public, max-age=300"
