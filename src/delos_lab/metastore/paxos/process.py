import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

import httpx
import uvicorn

from delos_lab.common.membership import validate_fixed_members

from .acceptor import PaxosAcceptor
from .client import PaxosMetaStore
from .http_api import create_paxos_app
from .http_transport import HttpPaxosTransport
from .proposer import PaxosProposer
from .sqlite_storage import SQLitePaxosStorage
from .state_machine import VersionRegisterStateMachine


def _peer_mapping(values: Sequence[str]) -> dict[str, str]:
    peers: dict[str, str] = {}
    for value in values:
        node_id, separator, url = value.partition("=")
        if not separator or not node_id or not url:
            raise ValueError(f"invalid peer mapping: {value}")
        if node_id in peers:
            raise ValueError("Paxos members must be unique non-empty identifiers")
        peers[node_id] = url
    validate_fixed_members(tuple(peers), label="Paxos")
    return peers


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Single-slot Paxos MetaStore peer")
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--peer", action="append", required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    return parser


async def serve_peer(
    *,
    node_id: str,
    peers: dict[str, str],
    database: Path,
    host: str,
    port: int,
) -> None:
    if node_id not in peers:
        raise ValueError("local node must be present in peer mappings")
    members = validate_fixed_members(tuple(sorted(peers)), label="Paxos")

    storage = SQLitePaxosStorage(database)
    await storage.open()
    client = httpx.AsyncClient(timeout=0.5)
    transport = HttpPaxosTransport(peers, client)
    acceptor = await PaxosAcceptor.create(
        node_id,
        storage,
        VersionRegisterStateMachine(),
    )
    proposer = PaxosProposer(
        node_id,
        members,
        acceptor,
        transport,
    )
    metastore = PaxosMetaStore(proposer, acceptor, members)
    server = uvicorn.Server(
        uvicorn.Config(
            create_paxos_app(node_id, acceptor, metastore),
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
        await client.aclose()
        await storage.close()


def main() -> None:
    arguments = _parser().parse_args()
    try:
        peers = _peer_mapping(arguments.peer)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    asyncio.run(
        serve_peer(
            node_id=arguments.node_id,
            peers=peers,
            database=arguments.db,
            host=arguments.host,
            port=arguments.port,
        )
    )


if __name__ == "__main__":
    main()
