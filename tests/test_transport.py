import asyncio
import sys

import pytest

from minecraft_structure_mcp.server import BearerGate, _is_loopback


def test_bearer_gate_rejects_missing_or_wrong_token():
    calls = []

    async def downstream(scope, receive, send):
        calls.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send_with(headers):
        result = []
        async def send(message):
            result.append(message)
        await BearerGate(downstream, "x" * 32)(
            {"type": "http", "headers": headers}, None, send)
        return result

    assert asyncio.run(send_with([]))[0]["status"] == 401
    assert asyncio.run(send_with([(b"authorization", b"Bearer wrong")]))[0]["status"] == 401
    assert asyncio.run(send_with([(b"authorization", b"Bearer " + b"x"*32)]))[0]["status"] == 200
    assert len(calls) == 1


def test_public_bind_requires_tls_and_token(monkeypatch):
    from minecraft_structure_mcp import server
    assert _is_loopback("127.0.0.1") and _is_loopback("::1")
    assert not _is_loopback("0.0.0.0")
    monkeypatch.delenv("STRUCTURE_MCP_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["mcp", "--transport", "streamable-http", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit) as error:
        server.main()
    assert error.value.code == 2
    monkeypatch.setenv("STRUCTURE_MCP_TOKEN", "z" * 48)
    monkeypatch.setattr(sys, "argv", ["mcp", "--transport", "streamable-http", "--host", "0.0.0.0",
                                   "--tls-cert", "/tmp/cert", "--tls-key", "/tmp/key"])
    with pytest.raises(SystemExit) as error:
        server.main()
    assert error.value.code == 2  # public host validation needs an explicit HTTPS URL
