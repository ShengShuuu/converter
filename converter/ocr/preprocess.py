"""Page image cleanup before OCR: EXIF orientation, red-ink removal, grayscale, dark-band inversion, contrast."""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

# Thresholds below were measured on the first real CamScanner batch (2026-10-16), not guessed.
# Red pen is R-dominant; printed text there has a blue cast and the PAID stamp is navy, so only red is separable.
RED_INK_MIN_REDNESS = 15
# Pen strokes have dark anti-aliased fringes that are not red enough on their own.
RED_INK_FRINGE_PX = 7
DARK_BAND_MAX_GRAY = 200
DARK_BAND_MIN_WIDTH = 300
DARK_BAND_HEIGHT_RANGE = (30, 200)
DARK_BAND_MIN_FILL = 0.6
# Wider and taller than any text stroke at 300 dpi, so only solid filled areas survive the opening.
DARK_BAND_KERNEL = (61, 25)

_ROTATE_CLOCKWISE = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def load_rgb(image_path: Path) -> np.ndarray:
    with Image.open(image_path) as image:
        return np.asarray(ImageOps.exif_transpose(image).convert("RGB"))


def remove_red_ink(rgb: np.ndarray) -> np.ndarray:
    """Whiten red pen marks; HSV saturation was tried first but is unstable on dark pixels and ate black text."""
    channels = rgb.astype(np.int16)
    redness = channels[:, :, 0] - np.maximum(channels[:, :, 1], channels[:, :, 2])
    fringe = np.ones((RED_INK_FRINGE_PX, RED_INK_FRINGE_PX), np.uint8)
    ink_mask = cv2.dilate((redness >= RED_INK_MIN_REDNESS).astype(np.uint8), fringe).astype(bool)
    cleaned = rgb.copy()
    cleaned[ink_mask] = 255
    return cleaned


def normalize_contrast(gray: np.ndarray) -> np.ndarray:
    return cv2.normalize(gray, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX)


def invert_dark_bands(gray: np.ndarray) -> np.ndarray:
    """Invert solid dark bars (e.g. the colored Total row) so their light text becomes dark-on-light."""
    dark = (gray < DARK_BAND_MAX_GRAY).astype(np.uint8) * 255
    solid = cv2.morphologyEx(dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, DARK_BAND_KERNEL))
    contours, _ = cv2.findContours(solid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = gray.copy()
    min_height, max_height = DARK_BAND_HEIGHT_RANGE
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if (
            width >= DARK_BAND_MIN_WIDTH
            and min_height <= height <= max_height
            and cv2.contourArea(contour) >= DARK_BAND_MIN_FILL * width * height
        ):
            # Otsu here turned gradient bars into solid blobs; a plain stretch keeps the text legible.
            out[y : y + height, x : x + width] = normalize_contrast(255 - gray[y : y + height, x : x + width])
    return out


def preprocess_image(rgb: np.ndarray) -> np.ndarray:
    gray = normalize_contrast(cv2.cvtColor(remove_red_ink(rgb), cv2.COLOR_RGB2GRAY))
    return invert_dark_bands(gray)


def rotate_clockwise(image: np.ndarray, degrees: int) -> np.ndarray:
    if degrees % 360 == 0:
        return image
    return cv2.rotate(image, _ROTATE_CLOCKWISE[degrees % 360])


def save_png(image: np.ndarray, output_path: Path) -> Path:
    Image.fromarray(image).save(output_path)
    return output_path
