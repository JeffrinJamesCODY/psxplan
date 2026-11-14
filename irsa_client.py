# backend/irsa_client.py
from astroquery.ipac.irsa import Irsa
from astropy.coordinates import SkyCoord
import astropy.units as u
import logging

logger = logging.getLogger(__name__)

def query_spherex(ra: float, dec: float, radius_arcsec: float = 60, collection: str = "spherex_qr2"):
    """
    Query IRSA SIA2 for SPHEREx images at a given coordinate.
    Returns an astropy Table or None.
    """
    coord = SkyCoord(ra, dec, unit="deg")
    try:
        # The SIA2 query returns a DALResultsTable which can be converted to a Table
        table = Irsa.query_sia(
            pos=(coord, radius_arcsec * u.arcsec),
            collection=collection
        ).to_table()
        return table
    except Exception as e:
        logger.error(f"IRSA query failed for ra={ra}, dec={dec}: {e}")
        return None