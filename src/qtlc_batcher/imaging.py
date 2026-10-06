"""Image primitives: load, grayscale, invert, background (white top-hat), matching the qTLC web app."""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def load_rgb(path, rotate: int = 0) -> np.ndarray:
    """Load an image as uint8 RGB. `rotate` is a multiple of 90 (degrees, counter-clockwise)."""
    rgb = np.asarray(Image.open(path).convert("RGB"))
    if rotate % 360:
        rgb = np.rot90(rgb, k=(rotate // 90) % 4)
    return np.ascontiguousarray(rgb)


def to_gray_u8(rgb: np.ndarray) -> np.ndarray:
    """MATLAB rgb2gray weighting (the paper states the app uses a weighted RGB sum), rounded to uint8."""
    f = rgb.astype(np.float64)
    return np.round(0.2989 * f[..., 0] + 0.5870 * f[..., 1] + 0.1140 * f[..., 2]).astype(np.uint8)


def invert(img: np.ndarray) -> np.ndarray:
    return 255 - img


def kernel_size_from_rect(w: float, h: float) -> int:
    """Web-app rule: the background kernel size is round(diagonal of the drawn rectangle / 2), in pixels.

    Established by probing the server (5 rectangle shapes, all columns exact). Ties are not resolved.
    """
    return max(1, int(round(float(np.hypot(round(w), round(h))) / 2)))


def tophat(img: np.ndarray, kernel_size: int) -> np.ndarray:
    """White top-hat exactly as the web app computes it: OpenCV MORPH_TOPHAT with an elliptical
    kernel of `kernel_size` x `kernel_size` pixels (even sizes included; that is what makes the result
    match the server pixel for pixel). `img` must be uint8 (inverted gray, bands bright on dark)."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    return cv2.morphologyEx(np.ascontiguousarray(img), cv2.MORPH_TOPHAT, k)
