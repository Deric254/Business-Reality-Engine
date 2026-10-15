"""Serves the built web app (frontend/dist) from the API service, so there is ONE URL and ONE thing to keep running.
Anything under /api, /docs, /redoc or /openapi.json is never touched; every other GET falls back to index.html so
client-side routes (/business, /alert/123 ...) survive a refresh."""
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

logger = logging.getLogger("business_reality_engine")

SPA_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
           "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
_RESERVED = ("api/", "docs", "redoc", "openapi.json", "healthz")


def find_dist(configured: str) -> Path | None:
    here = Path(__file__).resolve().parent.parent
    for c in [configured, str(here / "frontend_dist"), str(here / "frontend" / "dist")]:
        if c and (Path(c) / "index.html").is_file():
            return Path(c).resolve()
    return None


def mount_spa(app: FastAPI, configured_dist: str = "") -> bool:
    dist = find_dist(configured_dist)
    if dist is None:
        logger.info("Web app not found next to the API (no frontend_dist/index.html); serving the API only")
        return False
    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str, request: Request):
        if full_path.startswith(_RESERVED):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        target = (dist / full_path).resolve()
        if full_path and target.is_file() and (target == dist or dist in target.parents):
            resp: Response = FileResponse(target)
            if full_path.startswith("assets/"):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp
        if "." in full_path.rsplit("/", 1)[-1]:      # a missing asset must be a 404, not an HTML page
            return JSONResponse({"detail": "Not found"}, status_code=404)
        resp = FileResponse(index)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    logger.info(f"Serving web app from {dist}")
    return True
