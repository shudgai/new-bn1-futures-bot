import asyncio
import json
from unittest.mock import AsyncMock, Mock

from services import api


def test_read_only_startup_never_starts_trading(monkeypatch):
    monkeypatch.setattr(api, "WEB_READ_ONLY", True)
    start = AsyncMock()
    monkeypatch.setattr(api.engine, "start", start)
    asyncio.run(api.startup_event())
    assert asyncio.run(api.recover_bot_if_needed()) is False
    start.assert_not_called()
    assert api._bot_supervisor_task is None


def test_read_only_gets_do_not_update_positions_and_posts_are_blocked(monkeypatch):
    monkeypatch.setattr(api, "WEB_READ_ONLY", True)
    update = AsyncMock(side_effect=AssertionError("read-only must not update positions"))
    monkeypatch.setattr(api.engine.account, "update_positions", update)

    async def check():
        async def request(method, path):
            messages = []
            received = False

            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                await asyncio.Event().wait()

            async def send(message):
                messages.append(message)

            await api.app({"type": "http", "asgi": {"version": "3.0"},
                           "http_version": "1.1", "method": method, "scheme": "http",
                           "path": path, "raw_path": path.encode(), "root_path": "",
                           "query_string": b"", "headers": [], "server": ("test", 80),
                           "client": ("127.0.0.1", 1000)}, receive, send)
            status = next(m["status"] for m in messages if m["type"] == "http.response.start")
            body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
            return status, body

        for path in ("/", "/api/status", "/api/prices", "/api/account-exposure"):
            status, body = await request("GET", path)
            assert status == 200
            if path in ("/api/status", "/api/prices"):
                assert json.loads(body)["web_read_only"] is True
                assert json.loads(body)["market_data_live"] is False
        for path in ("/api/toggle", "/api/manual_order", "/api/manual_close",
                     "/api/reset_account", "/api/ai-trade-analysis"):
            status, body = await request("POST", path)
            assert status == 403
            assert json.loads(body)["code"] == "WEB_READ_ONLY"

    asyncio.run(check())
    update.assert_not_called()


def test_normal_mode_keeps_existing_position_updates(monkeypatch):
    monkeypatch.setattr(api, "WEB_READ_ONLY", False)
    update = AsyncMock(return_value=12.5)
    monkeypatch.setattr(api.engine.account, "update_positions", update)
    assert asyncio.run(api.displayed_unrealized_pnl()) == 12.5
    update.assert_awaited_once_with(api.engine.tickers)


def test_runtime_source_is_captured_at_startup_not_read_from_later_files(monkeypatch):
    from services import runtime_source
    monkeypatch.setattr(api, "WEB_READ_ONLY", True)
    monkeypatch.setattr(api, "_runtime_source_at_boot", None)
    boot = {"commit": "boot-commit", "tree_clean_at_boot": True,
            "source_hashes": {"core/engine.py": "boot-hash"}}
    capture = Mock(return_value=boot)
    monkeypatch.setattr(runtime_source, "capture_runtime_source", capture)
    asyncio.run(api.startup_event())
    capture.return_value = {"commit": "changed-after-start"}
    response = asyncio.run(api.get_runtime_source())
    assert json.loads(response.body)["commit"] == "boot-commit"
    assert json.loads(response.body)["web_read_only"] is True
    capture.assert_called_once()
