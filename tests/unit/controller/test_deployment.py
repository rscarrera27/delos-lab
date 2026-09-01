from pathlib import Path

from delos_lab.controller.deployment import StaticNodeDirectory
from delos_lab.controller.manifest import LabManifest


def test_static_directory_projects_subprocess_nodes_as_nodes(tmp_path: Path) -> None:
    manifest = LabManifest.create_subprocess(
        tmp_path,
        meta_ports=(9201, 9202, 9203),
        db_ports=(9301, 9302, 9303, 9304, 9305),
        database_ids=("db-1", "db-2", "db-3", "db-4", "db-5"),
    )

    directory = StaticNodeDirectory(manifest)

    assert directory.configured == {"meta": 3, "db": 5}
    assert tuple(node.node_id for node in directory.nodes("meta")) == (
        "meta-1",
        "meta-2",
        "meta-3",
    )
    db_five = directory.require("db-5")
    assert db_five.service == "db"
    assert db_five.endpoint == "http://127.0.0.1:9305"
    assert db_five.database == tmp_path.resolve() / "db-5.sqlite3"


def test_static_directory_exposes_the_original_manifest(tmp_path: Path) -> None:
    manifest = LabManifest.create(tmp_path, ports=(1, 2, 3, 4, 5, 6))

    assert StaticNodeDirectory(manifest).manifest == manifest


def test_static_directory_omits_retired_database_processes(tmp_path: Path) -> None:
    manifest = LabManifest.create(
        tmp_path,
        ports=(1, 2, 3, 4, 5, 6),
        database_ids=("db-1", "db-2", "db-3"),
    )
    manifest.retire_database_node("db-2")
    directory = StaticNodeDirectory(manifest)

    assert directory.configured["db"] == 2
    assert tuple(node.node_id for node in directory.nodes("db")) == ("db-1", "db-3")
