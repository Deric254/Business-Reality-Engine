"""The web app is served by the API: client-side routes survive a refresh, built assets are cached for good, and the
installable-app files (service worker, manifest) are always revalidated so an old release can never stay alive."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.spa import mount_spa


@pytest.fixture()
def web(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>app</title>")
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log(1)")
    (tmp_path / "sw.js").write_text("self.addEventListener('fetch',()=>{})")
    (tmp_path / "manifest.webmanifest").write_text('{"name":"x"}')
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    app = FastAPI()
    assert mount_spa(app, str(tmp_path)) is True
    return TestClient(app)


def test_client_side_routes_and_the_start_page_are_never_cached(web):
    for path in ("/", "/business", "/alert/123"):
        r = web.get(path)
        assert r.status_code == 200 and "<title>app</title>" in r.text and r.headers["cache-control"] == "no-cache"


def test_hashed_assets_are_cached_for_good_and_other_files_are_not(web):
    assert "immutable" in web.get("/assets/app-abc123.js").headers["cache-control"]
    assert "immutable" not in web.get("/logo.png").headers.get("cache-control", "")


def test_the_service_worker_and_manifest_are_always_revalidated_and_the_manifest_has_its_type(web):
    sw, mf = web.get("/sw.js"), web.get("/manifest.webmanifest")
    assert sw.status_code == mf.status_code == 200
    assert sw.headers["cache-control"] == mf.headers["cache-control"] == "no-cache"
    assert mf.headers["content-type"].startswith("application/manifest+json")


def test_head_requests_work_for_the_app_and_its_files(web):
    for path in ("/", "/sw.js", "/assets/app-abc123.js"):
        r = web.head(path)
        assert r.status_code == 200 and r.content == b""


def test_a_missing_file_is_a_404_not_the_app_and_the_api_namespace_is_left_alone(web):
    assert web.get("/assets/missing.js").status_code == 404
    assert web.get("/api/anything").status_code == 404 and web.get("/api/anything").json() == {"detail": "Not found"}


def test_a_path_that_climbs_out_of_the_folder_serves_the_app_never_a_file(web):
    r = web.get("/..%2f..%2fetc%2fpasswd")
    assert r.status_code in (200, 404) and "root:" not in r.text


def test_the_shipped_manifest_and_worker_exist_and_agree_with_the_icons():
    import json
    from pathlib import Path
    public = Path(__file__).resolve().parent.parent / "frontend" / "public"
    manifest = json.loads((public / "manifest.webmanifest").read_text())
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
    sizes = {i["sizes"] for i in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in manifest["icons"]:
        assert (public / icon["src"].lstrip("/")).is_file(), icon["src"]
    worker = (public / "sw.js").read_text()
    assert '"/api/"' in worker                              # the API is never handled by the worker
