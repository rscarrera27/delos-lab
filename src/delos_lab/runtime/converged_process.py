import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn

from delos_lab.common.membership import validate_fixed_members
from delos_lab.kv.bootstrap import DatabaseReplicaBootstrapper
from delos_lab.kv.materializer import KvMaterializer
from delos_lab.kv.service import KvService
from delos_lab.kv.sqlite_store import SQLiteKvStore
from delos_lab.metastore.paxos.client import PaxosMetaStoreClient
from delos_lab.metastore.paxos.http_transport import HttpMetaStorePeer
from delos_lab.native_loglet.http_transport import HttpLogletTransport
from delos_lab.native_loglet.membership import NativeLogletStorageMembership
from delos_lab.native_loglet.reconfiguration import (
    HttpIncarnationDirectory,
    NativeLogletReconfigurationPolicy,
)
from delos_lab.native_loglet.sequencer_registry import LogServerSequencerRegistry
from delos_lab.native_loglet.server import NativeLogServer
from delos_lab.native_loglet.sqlite_store import SQLiteLogletStore
from delos_lab.native_loglet.virtual_log_adapter import (
    HttpNativeLogletProvider,
    HttpSequencerTransport,
)
from delos_lab.virtual_log.core import VirtualLog

from .converged_http import create_converged_app
from .database_bootstrap import HttpDatabaseSnapshotSource
from .peer_directory import ManifestEndpointDirectory


def parse_mapping(values: Sequence[str], *, label: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for value in values:
        node_id, separator, url = value.partition("=")
        if not separator or not node_id or not url:
            raise ValueError(f"invalid {label} mapping: {value}")
        if node_id in mapping:
            raise ValueError(f"{label} members must be unique non-empty identifiers")
        mapping[node_id] = url
    validate_fixed_members(tuple(mapping), label=label)
    return mapping


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Converged Delos database peer")
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--db-peer", action="append", required=True)
    parser.add_argument("--meta-peer", action="append", required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--join-existing-database", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    return parser


async def serve_db_peer(
    *,
    node_id: str,
    db_peers: dict[str, str],
    meta_peers: dict[str, str],
    database: Path,
    host: str,
    port: int,
    join_existing_database: bool = False,
    manifest: Path | None = None,
) -> None:
    if node_id not in db_peers:
        raise ValueError("local node must be present in DB mappings")
    members = validate_fixed_members(tuple(sorted(db_peers)), label="DB")
    incarnation_id = str(uuid4())
    loglet_store = SQLiteLogletStore(node_id, database)
    kv_store = SQLiteKvStore(database)
    await loglet_store.open()
    await kv_store.open()
    log_server = NativeLogServer(loglet_store)
    client = httpx.AsyncClient(timeout=0.75)
    endpoints = ManifestEndpointDirectory(manifest, db_peers) if manifest is not None else db_peers
    loglet_transport = HttpLogletTransport(endpoints, client)
    sequencer_transport = HttpSequencerTransport(endpoints, client)
    provider = HttpNativeLogletProvider(node_id, loglet_transport, sequencer_transport)
    metastore = PaxosMetaStoreClient(
        {peer: HttpMetaStorePeer(url, client) for peer, url in meta_peers.items()}
    )
    incarnations = HttpIncarnationDirectory(endpoints, client)
    reconfiguration = NativeLogletReconfigurationPolicy(
        members,
        incarnations,
    )
    virtual_log = VirtualLog(metastore, provider, reconfiguration)
    service = KvService(
        node_id,
        virtual_log,
        KvMaterializer(virtual_log, kv_store),
        kv_store,
        reconfiguration,
    )
    if join_existing_database:
        sources = tuple(url for peer, url in db_peers.items() if peer != node_id)
        await DatabaseReplicaBootstrapper(
            kv_store,
            HttpDatabaseSnapshotSource(sources, client),
            service,
        ).run()
    registry = LogServerSequencerRegistry(node_id, incarnation_id, loglet_transport)
    app = create_converged_app(
        node_id=node_id,
        incarnation_id=incarnation_id,
        service=service,
        loglet_observer=provider,
        registry=registry,
        log_server=log_server,
        storage_membership=NativeLogletStorageMembership(
            node_id,
            virtual_log,
            incarnations,
            endpoints if isinstance(endpoints, ManifestEndpointDirectory) else None,
        ),
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            lifespan="off",
        )
    )
    try:
        await server.serve()
    finally:
        await registry.close()
        await client.aclose()
        await kv_store.close()
        await loglet_store.close()


def main() -> None:
    arguments = _parser().parse_args()
    try:
        db_peers = parse_mapping(arguments.db_peer, label="DB")
        meta_peers = parse_mapping(arguments.meta_peer, label="MetaStore")
    except ValueError as error:
        raise SystemExit(str(error)) from error
    asyncio.run(
        serve_db_peer(
            node_id=arguments.node_id,
            db_peers=db_peers,
            meta_peers=meta_peers,
            database=arguments.db,
            host=arguments.host,
            port=arguments.port,
            join_existing_database=arguments.join_existing_database,
            manifest=arguments.manifest,
        )
    )


if __name__ == "__main__":
    main()
