from pathlib import Path

import httpx

from delos_lab.controller.manifest import LabManifest
from delos_lab.controller.process import build_controller_app


def runtime_with_manifest(path: Path) -> Path:
    manifest = LabManifest.create(path, ports=(19401, 19402, 19403, 19501, 19502, 19503))
    manifest.save()
    return path


async def test_controller_serves_built_spa_without_shadowing_api(tmp_path: Path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<h1>Delos Lab SPA</h1>", encoding="utf-8")

    app = build_controller_app(
        runtime_with_manifest(tmp_path / "runtime"), auto_start=False, static_dir=static_dir
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://controller"
    ) as client:
        root = await client.get("/")
        health = await client.get("/api/health")

    assert root.status_code == 200
    assert "Delos Lab SPA" in root.text
    assert health.json() == {"status": "ok"}


async def test_controller_has_api_only_when_spa_is_not_built(tmp_path: Path) -> None:
    app = build_controller_app(
        runtime_with_manifest(tmp_path / "runtime"),
        auto_start=False,
        static_dir=tmp_path / "missing",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://controller"
    ) as client:
        assert (await client.get("/api/health")).status_code == 200
        assert (await client.get("/")).status_code == 404
