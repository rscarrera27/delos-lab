import re
from pathlib import Path

import pytest

from delos_lab.controller.manifest import LabManifest
from delos_lab.controller.process import _parser, build_controller_app


def test_arguments_accept_independent_fixed_node_counts() -> None:
    arguments = _parser().parse_args(["--meta-nodes", "3", "--db-nodes", "5", "--no-auto-start"])

    assert arguments.meta_nodes == 3
    assert arguments.db_nodes == 5
    assert arguments.no_auto_start is True


def test_controller_creates_and_reuses_local_process_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "delos_lab.controller.process._available_ports",
        lambda count: tuple(range(10001, 10001 + count)),
    )
    build_controller_app(
        tmp_path,
        auto_start=False,
        meta_nodes=3,
        db_nodes=5,
        static_dir=tmp_path / "missing",
    )

    manifest = LabManifest.load(tmp_path / "manifest.json")
    assert len(manifest.metastore_nodes) == 3
    assert len(manifest.database_nodes) == 5
    assert all(re.fullmatch(r"db-[a-z0-9]{5}", node.node_id) for node in manifest.database_nodes)

    build_controller_app(
        tmp_path,
        auto_start=False,
        meta_nodes=3,
        db_nodes=5,
        static_dir=tmp_path / "missing",
    )


def test_controller_rejects_saved_manifest_with_different_node_count(
    tmp_path: Path,
) -> None:
    manifest = LabManifest.create(tmp_path, ports=(1, 2, 3, 4, 5, 6))
    manifest.save()

    with pytest.raises(ValueError, match="database node count"):
        build_controller_app(
            tmp_path,
            auto_start=False,
            meta_nodes=3,
            db_nodes=5,
            static_dir=tmp_path / "missing",
        )
