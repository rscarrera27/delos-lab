from pathlib import Path

import pytest

from delos_lab.controller.manifest import LabManifest


def test_manifest_allocates_three_meta_and_three_db_nodes(tmp_path: Path) -> None:
    manifest = LabManifest.create(
        tmp_path,
        ports=(9201, 9202, 9203, 9301, 9302, 9303),
        database_ids=("db-1", "db-2", "db-3"),
    )

    assert tuple(manifest.nodes) == (
        "meta-1",
        "meta-2",
        "meta-3",
        "db-1",
        "db-2",
        "db-3",
    )
    assert manifest.nodes["db-1"].database == tmp_path / "db-1.sqlite3"
    assert manifest.nodes["meta-3"].endpoint == "http://127.0.0.1:9203"


def test_manifest_round_trips_json(tmp_path: Path) -> None:
    manifest = LabManifest.create(
        tmp_path, ports=(1, 2, 3, 4, 5, 6), database_ids=("db-1", "db-2", "db-3")
    )

    manifest.save()

    assert LabManifest.load(tmp_path / "manifest.json") == manifest


def test_manifest_supports_independent_three_meta_and_five_db_nodes(tmp_path: Path) -> None:
    manifest = LabManifest.create_subprocess(
        tmp_path,
        meta_ports=(9201, 9202, 9203),
        db_ports=(9301, 9302, 9303, 9304, 9305),
        database_ids=("db-1", "db-2", "db-3", "db-4", "db-5"),
    )

    assert tuple(node.node_id for node in manifest.metastore_nodes) == (
        "meta-1",
        "meta-2",
        "meta-3",
    )
    assert tuple(node.node_id for node in manifest.database_nodes) == (
        "db-1",
        "db-2",
        "db-3",
        "db-4",
        "db-5",
    )
    assert manifest.nodes["meta-3"].endpoint == "http://127.0.0.1:9203"
    assert manifest.nodes["db-5"].database == tmp_path.resolve() / "db-5.sqlite3"


def test_manifest_accepts_even_service_membership_with_majority_quorums(tmp_path: Path) -> None:
    manifest = LabManifest.create_subprocess(
        tmp_path,
        meta_ports=(9201, 9202, 9203),
        db_ports=(9301, 9302, 9303, 9304),
        database_ids=("db-1", "db-2", "db-3", "db-4"),
    )

    assert len(manifest.database_nodes) == 4


def test_manifest_tracks_a_database_node_join_until_storage_membership_is_installed(
    tmp_path: Path,
) -> None:
    manifest = LabManifest.create(
        tmp_path, ports=(1, 2, 3, 4, 5, 6), database_ids=("db-1", "db-2", "db-3")
    )

    node = manifest.add_database_node("http://127.0.0.1:7")

    assert node.node_id.startswith("db-")
    assert len(node.node_id) == 8
    assert node.join_existing_database is True
    assert manifest.pending_database_node == node
    assert LabManifest.load(manifest.path).pending_database_node == node

    with pytest.raises(RuntimeError, match="already pending"):
        manifest.add_database_node("http://127.0.0.1:8")

    joined = manifest.mark_database_node_joined(node.node_id)

    assert joined.join_existing_database is False
    assert manifest.pending_database_node is None


def test_retired_database_identity_is_hidden_but_reserved_in_manifest(tmp_path: Path) -> None:
    manifest = LabManifest.create(
        tmp_path, ports=(1, 2, 3, 4, 5, 6), database_ids=("db-1", "db-2", "db-3")
    )

    retired = manifest.retire_database_node("db-2")
    replacement = manifest.add_database_node("http://127.0.0.1:7")

    assert retired.retired is True
    assert tuple(node.node_id for node in manifest.active_database_nodes) == (
        "db-1",
        "db-3",
        replacement.node_id,
    )
    assert "db-2" in manifest.nodes
    assert replacement.node_id != "db-2"
