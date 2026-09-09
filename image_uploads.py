"""Validated image uploads to the shared bucket.

Extracted from routers/services.py so the business gallery does not carry a
second copy of the validation. The important part is that the type is decided
from the file's own leading bytes, not from its name or the content-type header
the caller sends: the bucket grants allUsers read, so a renamed executable or an
SVG carrying a script would otherwise be stored and served from our domain.
"""
import os
import uuid
from urllib.parse import quote, unquote

from fastapi import HTTPException

# Same bucket the animal photos use.
GCS_BUCKET = "oatmeal-farm-network-images"
GCS_PREFIX = f"https://storage.googleapis.com/{GCS_BUCKET}/"

ALLOWED_IMAGE_TYPES = {
    "image/webp": ".webp", "image/jpeg": ".jpg",
    "image/png": ".png", "image/gif": ".gif",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB

# Leading bytes of each accepted format, as hex to keep the signatures free of
# backslash escapes.
_MAGIC = (
    (bytes.fromhex("ffd8ff"), "image/jpeg"),
    (bytes.fromhex("89504e470d0a1a0a"), "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_image_type(data: bytes):
    """The real image type of these bytes, or None if it is not one we accept."""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    # WEBP is "RIFF" + 4 size bytes + "WEBP".
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def upload_image(file_bytes: bytes, folder: str) -> str:
    """Store the bytes under folder/ in the shared bucket, returning the URL.

    Raises HTTPException for anything that is not an accepted image, so callers
    can pass the result of file.read() straight in.
    """
    if not file_bytes:
        raise HTTPException(status_code=400, detail="The file is empty.")
    if len(file_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Images must be 10 MB or smaller.")
    content_type = sniff_image_type(file_bytes)
    if not content_type:
        raise HTTPException(
            status_code=400,
            detail="Only JPEG, PNG, GIF and WEBP images can be uploaded.")

    from google.cloud import storage as _gcs
    # The extension comes from the sniffed type, not the caller's filename.
    fname = f"{uuid.uuid4().hex}{ALLOWED_IMAGE_TYPES[content_type]}"
    blob = _gcs.Client().bucket(GCS_BUCKET).blob(f"{folder}/{fname}")
    blob.upload_from_string(file_bytes, content_type=content_type)
    return f"{GCS_PREFIX}{folder}/{quote(fname, safe='')}"


def delete_image(url: str) -> bool:
    """Remove a stored image. Best effort: never raises.

    Deleting the row and leaving the object behind is how the animal photos have
    accumulated. Failure here must not fail the request that removed the row --
    an object that outlives its row is untidy, a 500 on delete is not.

    Only touches our own bucket, and only the exact object named by the URL.
    """
    if not url or not url.startswith(GCS_PREFIX):
        return False
    name = unquote(url[len(GCS_PREFIX):])
    if not name or name.startswith('/') or '..' in name:
        return False
    try:
        from google.cloud import storage as _gcs
        _gcs.Client().bucket(GCS_BUCKET).blob(name).delete()
        return True
    except Exception as e:  # already gone, or transient — the row is what matters
        print("could not delete %s: %s" % (name, type(e).__name__))
        return False
