import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "delos_lab"


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    observed: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            observed.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            observed.add(node.module)
    return observed


def async_methods(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return {node.name for node in selected.body if isinstance(node, ast.AsyncFunctionDef)}


def test_virtual_log_has_no_concrete_loglet_or_controller_dependency() -> None:
    forbidden = ("delos_lab.native_loglet", "delos_lab.controller", "delos_lab.kv")
    for path in (ROOT / "virtual_log").glob("*.py"):
        assert not any(dependency.startswith(forbidden) for dependency in imports(path)), path


def test_delos_core_never_imports_the_lab_controller() -> None:
    for package in ("virtual_log", "native_loglet", "metastore", "kv"):
        for path in (ROOT / package).rglob("*.py"):
            assert not any(
                dependency.startswith("delos_lab.controller") for dependency in imports(path)
            ), path


def test_protocol_packages_never_import_the_executable_runtime() -> None:
    for package in ("virtual_log", "native_loglet", "metastore", "kv"):
        for path in (ROOT / package).rglob("*.py"):
            assert not any(
                dependency.startswith("delos_lab.runtime") for dependency in imports(path)
            ), path


def test_controller_does_not_import_delos_protocol_packages() -> None:
    for path in (ROOT / "controller").glob("*.py"):
        assert not any(
            dependency.startswith(
                (
                    "delos_lab.virtual_log",
                    "delos_lab.native_loglet",
                    "delos_lab.metastore",
                    "delos_lab.kv",
                )
            )
            for dependency in imports(path)
        ), path


def test_loglet_storage_does_not_own_transport_or_notification_lifecycle() -> None:
    for name in ("store.py", "memory_store.py", "sqlite_store.py"):
        dependencies = imports(ROOT / "native_loglet" / name)
        assert not any(
            dependency.startswith(("fastapi", "httpx", "delos_lab.runtime"))
            for dependency in dependencies
        ), name
        assert not any(dependency.endswith(".server") for dependency in dependencies), name


def test_virtual_log_owns_the_metastore_port_without_importing_implementations() -> None:
    for path in (ROOT / "virtual_log").glob("*.py"):
        assert not any(
            dependency.startswith("delos_lab.metastore") for dependency in imports(path)
        ), path


def test_kv_service_depends_on_the_generic_loglet_contract() -> None:
    assert not any(
        dependency.startswith("delos_lab.native_loglet")
        for dependency in imports(ROOT / "kv" / "service.py")
    )


def test_kv_http_api_does_not_assemble_a_concrete_loglet() -> None:
    assert not any(
        dependency.startswith("delos_lab.native_loglet")
        for dependency in imports(ROOT / "kv" / "http_api.py")
    )


def test_native_observation_does_not_expand_the_shared_loglet_contract() -> None:
    contract = (ROOT / "virtual_log" / "loglet.py").read_text(encoding="utf-8")
    service = (ROOT / "kv" / "service.py").read_text(encoding="utf-8")
    assert "known_tail(self)" not in contract
    assert "last_check_tail(self)" not in contract
    assert "client_known_tail" not in service
    assert "last_check_tail" not in service


def test_figure_two_loglet_and_virtual_log_interfaces_are_complete() -> None:
    loglet_api = {"append", "check_tail", "read_next", "prefix_trim", "seal"}
    virtual_log_api = loglet_api | {
        "reconfig_extend",
        "reconfig_truncate",
        "reconfig_modify",
    }

    assert async_methods(ROOT / "virtual_log" / "loglet.py", "VirtualLoglet") == loglet_api
    assert virtual_log_api <= async_methods(ROOT / "virtual_log" / "core.py", "VirtualLog")


def test_replica_is_not_used_as_a_generic_process_or_logserver_name() -> None:
    for path in (ROOT / "controller").glob("*.py"):
        assert "Replica" not in path.read_text(encoding="utf-8"), path
    for path in (ROOT / "native_loglet").glob("*.py"):
        assert "ReplicaState" not in path.read_text(encoding="utf-8"), path
