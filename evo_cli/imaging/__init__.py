from evo_cli.imaging.errors import ImagingError

IMAGE_EXTRA_HINT = "Pillow and numpy are required for image work.\nInstall them with:\n  pip install evo_cli[image]"


def load_pillow():
    try:
        from PIL import Image
    except ImportError as exc:
        raise ImagingError(IMAGE_EXTRA_HINT) from exc
    Image.MAX_IMAGE_PIXELS = None
    return Image


def load_numpy():
    try:
        import numpy
    except ImportError as exc:
        raise ImagingError(IMAGE_EXTRA_HINT) from exc
    return numpy


__all__ = [
    "IMAGE_EXTRA_HINT",
    "ImagingError",
    "load_numpy",
    "load_pillow",
]
