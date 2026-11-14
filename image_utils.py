# backend/image_utils.py
import io
import matplotlib
matplotlib.use("Agg")  # Must be set before importing pyplot
import matplotlib.pyplot as plt
from astropy.io import fits
from astropy.visualization import ImageNormalize, PercentileInterval, AsinhStretch
import logging

logger = logging.getLogger(__name__)

def fits_to_png(fits_bytes: bytes, ext: int = 1) -> bytes:
    """
    Convert a FITS file (bytes) to a PNG image (bytes).
    SPHEREx MEFs typically have the science image in extension 1.
    """
    try:
        with fits.open(io.BytesIO(fits_bytes)) as hdul:
            # SPHEREx files: 0=Primary, 1=IMAGE, 2=FLAGS, etc.
            # We default to ext=1 to grab the actual image data.
            data = hdul[ext].data.astype("float32")
    except Exception as e:
        logger.error(f"Failed to read FITS data: {e}")
        raise ValueError(f"Could not read FITS extension {ext}")

    # Normalize the data for display using a percentile stretch
    norm = ImageNormalize(
        data,
        interval=PercentileInterval(99.5),
        stretch=AsinhStretch()
    )

    fig, ax = plt.subplots(figsize=(4, 4), dpi=100)
    ax.imshow(data, norm=norm, cmap="gray", origin="lower")
    ax.axis("off")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0)
    plt.close(fig)  # CRITICAL: Prevents memory leaks
    buf.seek(0)
    return buf.getvalue()