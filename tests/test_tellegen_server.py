"""Native Study adapter contracts and path boundaries."""
import asyncio
import pytest
from powermcp import tellegen


def test_apply_is_not_an_agent_operation(monkeypatch):
    async def forbidden(*args):
        pytest.fail("rejected operation reached the native process")
    monkeypatch.setattr(tellegen, "_call", forbidden)
    with pytest.raises(ValueError, match="explicit native CLI"):
        asyncio.run(tellegen.study_run("study.json", 0, {"kind": "apply"}))


def test_paths_checked_before_native_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERIO_MCP_ALLOWED_ROOTS", str(tmp_path))
    async def forbidden(*args):
        pytest.fail("out-of-root path reached the native process")
    monkeypatch.setattr(tellegen, "_call", forbidden)
    with pytest.raises(ValueError):
        asyncio.run(tellegen.study_inspect(str(tmp_path.parent / "outside.json")))


def test_mutation_keeps_revision_binding_and_returns_compact_result(tmp_path, monkeypatch):
    path = tmp_path / "study.json"
    path.write_text("{}")
    received = []
    async def native(args, request):
        received.append((args, request))
        return {"summary": {"id": "s", "revision": 4, "active_goal": ["g", {"request": "lower prices", "anchor_state": "a", "objective": {"large": []}}]}, "experiment": "e", "inspected_view": {"large": []}}
    monkeypatch.setattr(tellegen, "_call", native)
    operation = {"kind": "propose", "state": "a", "goal": "g"}
    result = asyncio.run(tellegen.study_run(str(path), 3, operation))
    assert received == [(["study", "run", str(path)], {"expected_revision": 3, "operation": operation})]
    assert result["revision"] == 4 and result["experiment"] == "e"
    assert "inspected_view" not in result and "objective" not in result["active_goal"]


def test_tool_contract_registers_native_study_operations():
    names = {t.name for t in asyncio.run(tellegen.mcp.list_tools())}
    assert names == {"study_contract", "study_create", "study_inspect", "study_run", "study_import", "study_export"}


def test_cancellation_requests_graceful_native_save(monkeypatch):
    async def run():
        started, terminated, saved = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Process:
            returncode = None
            async def communicate(self, data):
                started.set()
                await terminated.wait()
                saved.set()
                self.returncode = 0
                return b'{"revision":1}', b''
            def terminate(self):
                terminated.set()
            def kill(self):
                pytest.fail("cooperative cancellation must allow the completed save")
            async def wait(self):
                return self.returncode
        async def spawn(*args, **kwargs):
            return Process()
        monkeypatch.setattr(tellegen, "_binary", lambda: "tellegen")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(tellegen._call(["study", "run", "study.json"], {}))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert terminated.is_set() and saved.is_set()
    asyncio.run(run())
