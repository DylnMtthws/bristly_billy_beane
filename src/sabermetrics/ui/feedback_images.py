"""Bounded, metadata-free raster images for issue reports."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

MAX_IMAGE_BYTES = 10_000_000
MAX_IMAGE_PIXELS = 25_000_000
FORMATS = {
    "PNG": ({".png"}, "image/png"),
    "JPEG": ({".jpg", ".jpeg"}, "image/jpeg"),
    "WEBP": ({".webp"}, "image/webp"),
}


@dataclass(frozen=True)
class FeedbackImage:
    content: bytes
    content_type: str
    extension: str


def sanitize_image(upload: FileStorage) -> FeedbackImage:
    raw = upload.read(MAX_IMAGE_BYTES + 1)
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Choose an image no larger than 10 MB.")
    try:
        with Image.open(BytesIO(raw), formats=list(FORMATS)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("Choose an image no larger than 25 megapixels.")
            kind = source.format or ""
            suffixes, mime = FORMATS[kind]
            if (
                PurePath(upload.filename or "").suffix.lower() not in suffixes
                or upload.mimetype != mime
                or getattr(source, "n_frames", 1) != 1
            ):
                raise ValueError("Choose a still PNG, JPEG, or WebP image.")
            source.load()
            # Apply orientation before discarding EXIF. Rebuild pixels in a
            # fresh image so EXIF, GPS, comments, ICC and XMP cannot survive.
            oriented = ImageOps.exif_transpose(source)
            mode = (
                "RGBA"
                if "A" in oriented.getbands() or "transparency" in oriented.info
                else "RGB"
            )
            pixels = oriented.convert(mode)
            clean = Image.new(mode, pixels.size)
            clean.paste(pixels)
            output = BytesIO()
            # Lossless output keeps small text in screenshots legible.
            clean.save(output, format="PNG")
            clean.close()
            pixels.close()
            if oriented is not source:
                oriented.close()
        content = output.getvalue()
        if len(content) > MAX_IMAGE_BYTES:
            raise ValueError(
                "This image is too large after processing. Crop it and try again."
            )
        return FeedbackImage(content, "image/png", "png")
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        KeyError,
    ):
        raise ValueError("Choose a valid still PNG, JPEG, or WebP image.") from None
