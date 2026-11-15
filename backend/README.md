# PSX Backend

FastAPI service that finds SPHEREx images covering a position on the sky, downloads small cutouts from NASA/IPAC IRSA, and serves them as PNGs for the frontend time-series viewer.

## How it works

1. The frontend sends an RA/Dec to `/api/images`.
2. The backend asks IRSA (SIA2 protocol, via `astroquery`) which SPHEREx spectral images overlap that position.
3. It returns the list sorted by observation time, with a ready-made `png` URL for each image.
4. When the frontend requests a `png` URL, the backend downloads a small cutout of the FITS file from IRSA (not the full ~70 MB frame), renders it to a grayscale PNG, caches it, and returns it.

## Requirements

- **Python 3.12** (3.13+ may fail to install `numpy`/`astropy`/`pyyaml` on Windows because prebuilt wheels are missing)
- Internet access to `irsa.ipac.caltech.edu`

## Setup

From the `backend/` folder:

```bash
py -3.12 -m venv .venv            # macOS/Linux: python3.12 -m venv .venv
source .venv/Scripts/activate     # macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Your prompt should start with `(.venv)`. Activate the venv again every time you open a new terminal.

## Run

```bash
python main.py
```

The server starts on http://localhost:8000. For auto-reload while developing:

```bash
uvicorn main:app --reload --port 8000
```

Interactive API docs (try endpoints in the browser): http://localhost:8000/docs

## API

### `GET /api/health`

Returns server status and cache sizes.

### `GET /api/images`

List SPHEREx images covering a position, sorted by time.

| Parameter | Type  | Default | Description                                |
|-----------|-------|---------|--------------------------------------------|
| `ra`      | float | required | Right ascension in degrees, 0 to <360      |
| `dec`     | float | required | Declination in degrees, -90 to 90          |
| `radius`  | float | 60      | Search radius in arcseconds (1 to 600)     |
| `cutout`  | float | 120     | Cutout size in arcseconds (10 to 1800)     |

Example: `/api/images?ra=269.452&dec=4.693`

Response:

```json
{
  "images": [
    {
      "id": "obs identifier",
      "url": "https://irsa.ipac.caltech.edu/...fits",
      "mjd": 60900.123,
      "date": "2025-06-01T02:57:07.200",
      "band_um": [0.75, 1.1],
      "png": "/api/image?url=...&ra=269.452&dec=4.693&size=120"
    }
  ]
}
```

- `mjd` and `date` may be `null` if IRSA has no time for that image.
- `band_um` is the wavelength range of the whole detector, in micrometres, **not** the wavelength at your target's pixel.
- `png` is a **relative** path. The frontend must prepend the backend's address: `API_URL + image.png`.

### `GET /api/image`

Returns a PNG rendered from an IRSA FITS image or cutout. You normally use the `png` value from `/api/images` rather than building this by hand.

| Parameter | Type  | Description                                              |
|-----------|-------|----------------------------------------------------------|
| `url`     | str   | IRSA FITS URL (must be https on `*.ipac.caltech.edu`)    |
| `ra`, `dec` | float | Cutout centre in degrees (optional)                    |
| `size`    | float | Cutout size in arcseconds (optional)                     |

If `ra`, `dec` and `size` are all given, only a cutout is downloaded. Otherwise the full frame is fetched, which is slow.

### Errors

| Status | Meaning                                              |
|--------|------------------------------------------------------|
| 400    | The `url` is not an allowed IRSA address             |
| 502    | IRSA is unreachable or returned an error             |
| 500    | The FITS file could not be rendered                  |

## Files

| File             | Purpose                                                        |
|------------------|----------------------------------------------------------------|
| `main.py`        | FastAPI app, endpoints, caching, startup prefetch              |
| `irsa_client.py` | IRSA SIA2 query and conversion of results to clean dicts       |
| `image_utils.py` | FITS to PNG rendering (numpy + Pillow)                         |
| `requirements.txt` | Pinned Python dependencies                                   |

## Behaviour notes

- **Caching:** results are cached in memory for 1 hour (image lists and PNGs separately). The cache is per process and is lost on restart. Run a single worker.
- **Startup prefetch:** a few demo targets are queried in the background on startup. If IRSA is down this only logs warnings; the server still starts.
- **CORS:** currently open to all origins for development. Restrict `allow_origins` in `main.py` to the frontend URL before deploying.

## Troubleshooting

- **`502 Proxy Error` / "Unable to access the capabilities endpoint":** IRSA's servers are temporarily failing. Check https://irsa.ipac.caltech.edu/SIA/capabilities in a browser, then wait and retry.
- **`Failed building wheel for PyYAML` / "Microsoft Visual C++ 14.0 required":** you are on a Python version that is too new. Recreate the venv with Python 3.12.
- **`ModuleNotFoundError`:** the venv is not activated, or you are not in the `backend/` folder.
- **Image is blank or a single colour:** the cutout may be off the detector or the target position may be wrong (see known issues).

## Known issues and TODO

- Column names from IRSA (`access_url`, `obs_id`, `t_min`, `em_min`, `em_max`) and the cutout size units still need verifying against live responses.
- No proper-motion correction yet. For fast-moving stars (for example Barnard's Star) cutouts centred on catalogue (J2000) positions will miss the object.
- No per-pixel wavelength yet (it lives in the `WCS-WAVE` extension of each file).
- PNGs are stretched independently, so brightness can flicker between frames.
- Deployment is untested. Serverless hosts are a poor fit because of the large dependencies and in-memory caches.
