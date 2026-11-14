# backend/main.py
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import requests
import logging
from cachetools import TTLCache
import threading

from irsa_client import query_spherex
from image_utils import fits_to_png

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Cache Setup ---
# Store up to 200 items, each valid for 1 hour (3600 seconds)
cache = TTLCache(maxsize=200, ttl=3600)
cache_lock = threading.Lock()

# --- Demo Targets to Prefetch on Startup ---
# Coordinates from the challenge examples (Approximate)
DEMO_TARGETS = [
    (269.452, 4.693),   # Barnard's Star
    (330.83, -56.79),   # Epsilon Indi (Example)
    (133.79, -10.18),   # WISE 0855 (Example)
]

def _get_cached_key(ra, dec, radius):
    return (round(ra, 4), round(dec, 4), radius)

def fetch_images_for_coord(ra, dec, radius):
    """Internal function to fetch and process image metadata."""
    key = _get_cached_key(ra, dec, radius)
    
    # Check cache first
    with cache_lock:
        if key in cache:
            logger.info(f"Cache hit for {key}")
            return cache[key]

    # Cache miss: Query IRSA
    logger.info(f"Querying IRSA for ra={ra}, dec={dec}, radius={radius}")
    table = query_spherex(ra, dec, radius)
    
    result = []
    if table is not None and len(table) > 0:
        # SIA2 results typically have 'access_url' and 'obs_id' or similar time info
        # We inspect columns to be safe, but fallback to access_url
        for row in table:
            url = row.get("access_url")
            if not url:
                continue
            
            # Try to find a date/time column. SIA2 often includes 't_min' or 'obs_id'
            # SPHEREx obs_id often contains the date context, or we use the URL as an ID
            date_val = str(row.get("t_min", row.get("obs_id", "unknown")))
            
            result.append({
                "url": str(url),
                "date": date_val,
                "id": str(row.get("obs_id", url)) # Unique identifier
            })
        
        # Sort by date to create the time-series order
        result.sort(key=lambda x: x["date"])

    # Store in cache
    with cache_lock:
        cache[key] = result
        
    return result

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Prefetch demo targets
    logger.info("Starting up: Prefetching demo targets...")
    for ra, dec in DEMO_TARGETS:
        try:
            fetch_images_for_coord(ra, dec, 60)
        except Exception as e:
            logger.warning(f"Prefetch failed for {ra},{dec}: {e}")
    yield
    # Shutdown logic (if any) goes here

app = FastAPI(title="SPHEREx Time-Series API", lifespan=lifespan)

# CORS Configuration
# Adjust origins if your frontend runs on a different port/domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For hackathon demo, allow all. Tighten for production.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health_check():
    return {"status": "ok", "cached_queries": len(cache)}

@app.get("/api/images")
def get_image_list(
    ra: float = Query(..., ge=0, lt=360),
    dec: float = Query(..., ge=-90, le=90),
    radius: float = Query(60, ge=1, le=600)
):
    """
    Returns a list of available SPHEREx images (URLs + dates) for a coordinate.
    """
    try:
        images = fetch_images_for_coord(ra, dec, radius)
        return {"images": images}
    except Exception as e:
        logger.error(f"Error fetching images: {e}")
        raise HTTPException(status_code=500, detail="Internal server error while querying IRSA")

@app.get("/api/image")
def get_image_png(url: str):
    """
    Fetches a FITS file from the given URL and returns it as a rendered PNG.
    """
    # Check cache for the rendered PNG
    png_key = ("png", url)
    with cache_lock:
        if png_key in cache:
            return Response(content=cache[png_key], media_type="image/png")

    # Fetch FITS from IRSA
    try:
        # Note: Use a longer timeout as IRSA can be slow
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to download FITS from {url}: {e}")
        raise HTTPException(status_code=502, detail="Failed to download FITS file from IRSA")

    # Convert to PNG
    try:
        # SPHEREx MEFs have image data in ext 1
        png_bytes = fits_to_png(resp.content, ext=1)
    except Exception as e:
        logger.error(f"Failed to render PNG: {e}")
        raise HTTPException(status_code=500, detail="Failed to render image")

    # Cache the PNG
    with cache_lock:
        cache[png_key] = png_bytes

    return Response(content=png_bytes, media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    # Run on 0.0.0.0 so it can be accessed from other containers/machines if needed
    uvicorn.run(app, host="0.0.0.0", port=8000)