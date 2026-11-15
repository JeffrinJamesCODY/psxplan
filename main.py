# backend/main.py
import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from urllib.parse import urlencode, urlparse

import requests
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from image_utils import fits_to_png
from irsa_client import IrsaError, query_spherex, table_to_images

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Config -----------------------------------------------------------------
ALLOWED_HOST_SUFFIX = ".ipac.caltech.edu"   # stops /api/image being an open proxy (SSRF)
MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024
DEFAULT_CUTOUT_ARCSEC = 120

DEMO_TARGETS = [
    (269.452, 4.693),    # Barnard's Star (J2000 position - it has moved ~4.5' since!)
    (330.84, -56.79),    # Epsilon Indi
    (133.79, -7.24),     # WISE 0855-0714  (your original Dec of -10.18 looks wrong)
]

# --- Caches (separate so PNGs can't evict metadata) --------------------------
meta_cache = TTLCache(maxsize=500, ttl=3600)
png_cache = TTLCache(maxsize=64 * 1024 * 1024, ttl=3600, getsizeof=len)  # sized in bytes
cache_lock = threading.Lock()

# --- HTTP session with connection pooling + retries --------------------------
session = requests.Session()
session.mount("https://", HTTPAdapter(
    pool_connections=10, pool_maxsize=20,
    max_retries=Retry(total=3, backoff_factor=0.5, status_forcelist=(502, 503, 504)),
))


def _cutout_url(url: str, ra: float | None, dec: float | None, size_arcsec: float | None) -> str:
    """IRSA cutout service: append ?center=ra,dec&size=<deg> to the image URL."""
    if ra is None or dec is None or not size_arcsec:
        return url  # full 2040x2040 frame (~70 MB!)
    qs = urlencode({"center": f"{ra:.6f},{dec:.6f}", "size": f"{size_arcsec / 3600:.6f}"}, safe=",")
    return f"{url}{'&' if '?' in url else '?'}{qs}"


def _check_irsa_url(url: str) -> None:
    p = urlparse(url)
    host = p.hostname or ""
    if p.scheme != "https" or not (host == ALLOWED_HOST_SUFFIX[1:] or host.endswith(ALLOWED_HOST_SUFFIX)):
        raise HTTPException(status_code=400, detail="url must be an https IRSA/IPAC URL")


def fetch_images_for_coord(ra: float, dec: float, radius: float, cutout_arcsec: float) -> list[dict]:
    key = (round(ra, 4), round(dec, 4), radius, cutout_arcsec)
    with cache_lock:
        hit = meta_cache.get(key)
    if hit is not None:
        logger.info("Metadata cache hit %s", key)
        return hit

    logger.info("Querying IRSA ra=%s dec=%s radius=%s", ra, dec, radius)
    table = query_spherex(ra, dec, radius)          # raises IrsaError -> never cached
    images = table_to_images(table)
    for im in images:
        im["png"] = "/api/image?" + urlencode({
            "url": im["url"], "ra": f"{ra:.6f}", "dec": f"{dec:.6f}", "size": cutout_arcsec,
        })

    with cache_lock:
        meta_cache[key] = images
    return images


def _prefetch():
    for ra, dec in DEMO_TARGETS:
        try:
            fetch_images_for_coord(ra, dec, 60, DEFAULT_CUTOUT_ARCSEC)
        except Exception as exc:
            logger.warning("Prefetch failed for %s,%s: %s", ra, dec, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Prefetch in the background so a slow/down IRSA never blocks server startup.
    task = asyncio.create_task(asyncio.to_thread(_prefetch))
    yield
    task.cancel()


app = FastAPI(title="SPHEREx Time-Series API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,   # "*" + credentials is invalid per the CORS spec; you use no cookies
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_check():
    return {"status": "ok", "cached_queries": len(meta_cache), "cached_pngs": len(png_cache)}


@app.get("/api/images")
def get_image_list(
    ra: float = Query(..., ge=0, lt=360),
    dec: float = Query(..., ge=-90, le=90),
    radius: float = Query(60, ge=1, le=600, description="search radius, arcsec"),
    cutout: float = Query(DEFAULT_CUTOUT_ARCSEC, ge=10, le=1800, description="cutout size, arcsec"),
):
    """List SPHEREx images covering a position, sorted by observation time."""
    try:
        return {"images": fetch_images_for_coord(ra, dec, radius, cutout)}
    except IrsaError:
        raise HTTPException(status_code=502, detail="IRSA query failed")


@app.get("/api/image")
def get_image_png(
    url: str,
    ra: float | None = Query(None, ge=0, lt=360),
    dec: float | None = Query(None, ge=-90, le=90),
    size: float | None = Query(None, ge=10, le=1800, description="cutout size, arcsec"),
):
    """Download a SPHEREx FITS (ideally just a cutout) and return a PNG."""
    _check_irsa_url(url)
    full_url = _cutout_url(url, ra, dec, size)

    with cache_lock:
        cached = png_cache.get(full_url)
    if cached is None:
        try:
            resp = session.get(full_url, timeout=(5, 60), stream=True)
            resp.raise_for_status()
            if int(resp.headers.get("Content-Length", 0)) > MAX_DOWNLOAD_BYTES:
                raise HTTPException(status_code=413, detail="FITS file too large")
            content = resp.content
        except requests.RequestException as exc:
            logger.error("Download failed %s: %s", full_url, exc)
            raise HTTPException(status_code=502, detail="Failed to download FITS from IRSA")

        try:
            cached = fits_to_png(content)
        except ValueError as exc:
            logger.error("Render failed %s: %s", full_url, exc)
            raise HTTPException(status_code=500, detail="Failed to render image")

        with cache_lock:
            png_cache[full_url] = cached

    return Response(content=cached, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
