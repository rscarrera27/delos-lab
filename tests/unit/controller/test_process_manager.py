import signal
from pathlib import Path

import httpx
import pytest

from delos_lab.controller.manifest import LabManifest
from delos_lab.controller.process_manager import SubprocessNodeSupervisor


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.signals: list[int] = []

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def send_signal(self, signal_number: int) -> None:
        self.signals.append(signal_number)

    async def wait(self) -> int:
        assert self.returncode is not None
        return self.returncode


class FakeLauncher:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    async def launch(self, command: tuple[str, ...], log_path: Path) -> FakeProcess:
        self.commands.append(command)
        return FakeProcess()


def manifest_with_named_databases(tmp_path: Path) -> LabManifest:
    return LabManifest.create(
        tmp_path,
        ports=(1, 2, 3, 4, 5, 6),
        database_ids=("db-1", "db-2", "db-3"),
    )


async def test_pause_and_resume_are_idempotent_and_preserve_process(tmp_path: Path) -> None:
    manifest = manifest_with_named_databases(tmp_path)
    launcher = FakeLauncher()
    manager = SubprocessNodeSupervisor(manifest, launcher=launcher)

    await manager.start_node("db-1")
    process = manager._processes["db-1"]
    await manager.pause_node("db-1")
    await manager.pause_node("db-1")
    paused = await manager.states(("db-1",))
    await manager.resume_node("db-1")
    await manager.resume_node("db-1")

    assert len(launcher.commands) == 1
    assert process.signals == [signal.SIGSTOP, signal.SIGCONT]
    assert paused["db-1"].lifecycle == "paused"
    assert manager.is_running("db-1")
    await manager.close()


async def test_subprocess_manager_implements_node_lifecycle_contract(tmp_path: Path) -> None:
    manifest = manifest_with_named_databases(tmp_path)
    manager = SubprocessNodeSupervisor(manifest, launcher=FakeLauncher())

    await manager.start_node("db-1")
    states = await manager.states(("db-1", "db-2"))
    await manager.stop_node("db-1")

    assert states["db-1"].lifecycle == "running"
    assert states["db-2"].lifecycle == "exited"
    await manager.close()


def test_commands_use_fixed_peer_mappings_and_database(tmp_path: Path) -> None:
    manifest = manifest_with_named_databases(tmp_path)
    manager = SubprocessNodeSupervisor(manifest, launcher=FakeLauncher())

    meta = manager.command_for("meta-1")
    database = manager.command_for("db-2")

    assert "delos_lab.metastore.paxos.process" in meta
    assert "--peer" in meta
    assert str(tmp_path.resolve() / "meta-1.sqlite3") in meta
    assert "delos_lab.runtime.converged_process" in database
    assert database.count("--db-peer") == 3
    assert database.count("--meta-peer") == 3


def test_commands_use_independent_three_meta_and_five_db_mappings(tmp_path: Path) -> None:
    manifest = LabManifest.create_subprocess(
        tmp_path,
        meta_ports=(1, 2, 3),
        db_ports=(4, 5, 6, 7, 8),
        database_ids=("db-1", "db-2", "db-3", "db-4", "db-5"),
    )
    manager = SubprocessNodeSupervisor(manifest, launcher=FakeLauncher())

    meta = manager.command_for("meta-3")
    database = manager.command_for("db-5")

    assert meta.count("--peer") == 3
    assert database.count("--db-peer") == 5
    assert database.count("--meta-peer") == 3
    assert "db-5=http://127.0.0.1:8" in database


async def test_add_database_node_bootstraps_then_joins_native_loglet_membership(
    tmp_path: Path,
) -> None:
    manifest = manifest_with_named_databases(tmp_path)
    manifest.add_database_node("http://127.0.0.1:7")
    launcher = FakeLauncher()
    requests: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"version": 2, "chain": {"segments": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        manager = SubprocessNodeSupervisor(manifest, launcher=launcher, client=client)

        node = await manager.add_database_node()

        command = launcher.commands[-1]
        assert node.node_id.startswith("db-")
        assert node.node_id not in {"db-1", "db-2", "db-3"}
        assert command.count("--db-peer") == 4
        assert "--join-existing-database" in command
        assert "--manifest" in command
        assert requests[-1].url.path == "/internal/native-loglet/storage-membership/join"
        assert manifest.pending_database_node is None
        await manager.close()


async def test_kill_retires_database_identity_and_removes_it_from_inventory(
    tmp_path: Path,
) -> None:
    manifest = manifest_with_named_databases(tmp_path)
    manager = SubprocessNodeSupervisor(manifest, launcher=FakeLauncher())
    await manager.start_node("db-2")

    await manager.kill_node("db-2")

    assert manifest.nodes["db-2"].retired is True
    assert tuple(node.node_id for node in manifest.active_database_nodes) == ("db-1", "db-3")
    assert manager.process_states()["db-2"]["running"] is False
    await manager.close()


async def test_kill_is_not_available_for_fixed_metastore_members(
    tmp_path: Path,
) -> None:
    manifest = manifest_with_named_databases(tmp_path)
    manager = SubprocessNodeSupervisor(manifest, launcher=FakeLauncher())
    await manager.start_node("meta-2")

    with pytest.raises(ValueError, match="only database processes"):
        await manager.kill_node("meta-2")
    states = await manager.states(("meta-2",))

    assert manifest.nodes["meta-2"].process_removed is False
    assert manifest.nodes["meta-2"].retired is False
    assert tuple(node.node_id for node in manifest.metastore_nodes) == (
        "meta-1",
        "meta-2",
        "meta-3",
    )
    assert states["meta-2"].lifecycle == "running"
    await manager.close()


async def test_reset_discards_managed_state_and_bootstraps_the_configured_size(
    tmp_path: Path,
) -> None:
    manifest = manifest_with_named_databases(tmp_path)
    manifest.save()
    old_database_ids = {node.node_id for node in manifest.database_nodes}
    old_database = manifest.nodes["db-1"].database
    old_database.write_text("old database", encoding="utf-8")
    Path(f"{old_database}-wal").write_text("old wal", encoding="utf-8")
    (tmp_path / "db-1.log").write_text("old log", encoding="utf-8")

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        manager = SubprocessNodeSupervisor(
            manifest,
            launcher=FakeLauncher(),
            client=client,
            port_allocator=lambda count: tuple(range(10, 10 + count)),
        )

        await manager.reset_cluster()

        new_database_ids = {node.node_id for node in manifest.database_nodes}
        assert new_database_ids.isdisjoint(old_database_ids)
        assert len(manifest.metastore_nodes) == 3
        assert len(manifest.database_nodes) == 3
        assert old_database.exists() is False
        assert Path(f"{old_database}-wal").exists() is False
        assert (tmp_path / "db-1.log").exists() is False
        assert LabManifest.load(manifest.path) == manifest
        assert all(manager.is_running(node_id) for node_id in manifest.nodes)
        await manager.close()
