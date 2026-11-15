# backend/irsa_client.py
import logging

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.time import Time
from astroquery.ipac.irsa import Irsa

logger = logging.getLogger(__name__)

# Run `Irsa.list_collections(filter="spherex")` to see what exists today
# (QR1 is retired; QR2 and QR3 are the current spectral-image releases).
DEFAULT_COLLECTION = "spherex_qr2"


class IrsaError(RuntimeError):
    """Raised when IRSA can't be queried. Never cache this."""


def query_spherex(ra: float, dec: float, radius_arcsec: float = 60,
                  collection: str = DEFAULT_COLLECTION, maxrec: int = 1000):
    """Query IRSA SIA2 for SPHEREx spectral images overlapping a position.

    Returns an astropy Table (possibly empty). Raises IrsaError on failure so
    callers can tell "no images" apart from "IRSA is down".
    """
    coord = SkyCoord(ra * u.deg, dec * u.deg, frame="icrs")
    try:
        res = Irsa.query_sia(
            pos=(coord, radius_arcsec * u.arcsec),
            collection=collection,
            maxrec=maxrec,
        )
    except Exception as exc:
        logger.exception("IRSA query failed for ra=%s dec=%s", ra, dec)
        raise IrsaError(str(exc)) from exc

    # astroquery <=0.4.9 returns a pyvo result (needs .to_table());
    # newer versions return a Table directly. Handle both.
    return res.to_table() if hasattr(res, "to_table") else res


def _num(row, col):
    """Return a float from a table row, or None if missing/masked/NaN."""
    if col not in row.colnames:
        return None
    val = row[col]
    if val is None or np.ma.is_masked(val):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def table_to_images(table) -> list[dict]:
    """Turn an SIA2 result table into a clean list of dicts, sorted by time."""
    images = []
    for row in table:
        url = row["access_url"] if "access_url" in row.colnames else None
        if not url or np.ma.is_masked(url):
            continue

        mjd = _num(row, "t_min")                # ObsCore: MJD (days)
        em_min = _num(row, "em_min")            # ObsCore: metres
        em_max = _num(row, "em_max")

        images.append({
            "id": str(row["obs_id"]) if "obs_id" in row.colnames else str(url),
            "url": str(url),
            "mjd": mjd,
            "date": Time(mjd, format="mjd").isot if mjd is not None else None,
            # NOTE: this is the whole-detector band, not the wavelength at your
            # target's pixel. Per-pixel wavelength lives in the WCS-WAVE extension.
            "band_um": [em_min * 1e6, em_max * 1e6]
                       if em_min is not None and em_max is not None else None,
        })

    # Numeric sort (the original sorted strings). Unknown dates go last.
    images.sort(key=lambda x: (x["mjd"] is None, x["mjd"] or 0.0))
    return images
