from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .models import Artwork, SourceId
from .storage import atomic_write
from .transport import FetchError, WebClient


@dataclass(frozen=True)
class ImageData:
    content: bytes
    format: str
    width: int
    height: int
    decoded: bool


def image_signature(data: bytes) -> bool:
    return data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")) or (
        data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    )


def validate_image(data: bytes, source: SourceId) -> ImageData:
    """Validate real image bytes; decode JavDB App's key-prefixed XOR envelope."""
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("artwork exceeds 32 MiB")
    decoded = False
    if not image_signature(data) and source == SourceId.JAVDB and len(data) > 16:
        key = data[0]
        if image_signature(bytes(value ^ key for value in data[1:17])):
            data = bytes(value ^ key for value in data[1:])
            decoded = True
    if not image_signature(data):
        raise ValueError("artwork response is not a supported image")
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            image_format = image.format
            image.verify()
        if not image_format or width < 1 or height < 1:
            raise ValueError("invalid image dimensions")
        return ImageData(data, image_format.lower(), width, height, decoded)
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError("artwork is corrupt") from exc


async def save_images(
    client: WebClient,
    items: list[Artwork],
    target: Path,
    stems: list[str],
    missing_only: bool = False,
) -> list[dict]:
    """Save validated original bytes, once per URL, under each media filename."""
    covers = [item for item in items if item.role == "cover"]
    posters = [item for item in items if item.role == "poster"]
    plan = []
    if posters or covers:
        plan.append((posters[0] if posters else covers[0], "poster"))
    if covers:
        plan.append((covers[0], "fanart"))
    samples = list({(item.source, item.url): item for item in items if item.role == "sample"}.values())
    plan.extend((item, f"fanart{i + 1}") for i, item in enumerate(samples[:100]))
    records, downloaded = [], {}

    def existing(base):
        return (
            [
                path
                for path in base.parent.iterdir()
                if path.stem == base.name
                and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
                and path.is_file()
                and not path.is_symlink()
            ]
            if base.parent.is_dir()
            else []
        )

    for item, role in plan:
        paths = [target / f"{stem}-{role}" for stem in stems]
        if missing_only and all(existing(path) for path in paths):
            continue
        try:
            identity = (item.source, item.url)
            if identity not in downloaded:
                downloaded[identity] = validate_image(
                    await client.image_bytes(item.source, item.url, item.referer), item.source
                )
            image = downloaded[identity]
            extension = ".jpg" if image.format == "jpeg" else "." + image.format
            for base in paths:
                if missing_only and existing(base):
                    continue
                # Append an extension: dotted video names must retain their full stem.
                path = base.parent / (base.name + extension)
                if not missing_only or not path.is_file():
                    atomic_write(path, image.content)
                    for old in existing(base):
                        if old != path and old.is_file() and not old.is_symlink():
                            old.unlink()
                records.append({"file": path.name, "status": "saved"})
        except (FetchError, ValueError, OSError) as exc:
            records.append({"file": role, "status": "error", "message": str(exc)})
    return records
