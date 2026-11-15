# backend/image_utils.py
import io
import logging

import numpy as np
from astropy.io import fits
from astropy.visualization import AsinhStretch, ImageNormalize, PercentileInterval
from PIL import Image

logger = logging.getLogger(__name__)


def fits_to_png(fits_bytes: bytes, extname: str = "IMAGE", min_size: int = 256) -> bytes:
    """Render one FITS image extension to an 8-bit grayscale PNG.

    Uses Pillow instead of matplotlib: ~10x faster, thread-safe, and the output
    is exactly one PNG pixel per FITS pixel (no figure/axes/margin surprises).
    """
    try:
        with fits.open(io.BytesIO(fits_bytes), memmap=False) as hdul:
            try:
                hdu = hdul[extname]
            except KeyError:
                hdu = hdul[1]  # fall back to first extension
            data = np.asarray(hdu.data, dtype=np.float32)
    except Exception as exc:
        logger.error("Failed to read FITS data: %s", exc)
        raise ValueError(f"Could not read FITS extension {extname!r}") from exc

    if data.ndim != 2 or not np.isfinite(data).any():
        raise ValueError("FITS extension has no usable 2D image data")

    norm = ImageNormalize(data, interval=PercentileInterval(99.5), stretch=AsinhStretch())
    scaled = np.ma.filled(norm(data), 0.0)           # masked / NaN pixels -> black
    scaled = np.nan_to_num(scaled, nan=0.0, posinf=1.0, neginf=0.0)
    img8 = (np.clip(scaled, 0.0, 1.0) * 255).astype(np.uint8)
    img8 = np.flipud(img8)                            # FITS origin is bottom-left

    img = Image.fromarray(img8, mode="L")
    if max(img.size) < min_size:                      # tiny cutouts: blow up, keep pixels crisp
        k = -(-min_size // max(img.size))             # ceil division
        img = img.resize((img.width * k, img.height * k), Image.NEAREST)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()
