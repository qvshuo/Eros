from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from .actor_names import comparison_name
from .logging_setup import report_progress
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


def validate_image(data: bytes, source: SourceId | None = None) -> ImageData:
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
    if covers or posters:
        plan.append((covers[0] if covers else posters[0], "fanart"))
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
                report_progress("images", source=item.source)
                downloaded[identity] = validate_image(
                    await client.image_bytes(item.source, item.url, item.referer),
                    item.source,
                )
            image = downloaded[identity]
            if role == "poster":
                import asyncio

                report_progress("crop")
                cropped = await asyncio.to_thread(crop_poster, image.content)
                image = validate_image(cropped["content"], item.source)
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


def crop_box(width: int, height: int, faces: list[list[float]]) -> tuple[int, int, int, int]:
    """Keep the largest 2:3 window; protect the main face with hair/chin margins."""
    unit = min(width // 2, height // 3)
    if unit < 1:
        raise ValueError("image is too small for a portrait cover")
    w, h = unit * 2, unit * 3
    x, y = (width - w) / 2, (height - h) / 2
    if faces:

        def score(face):
            fx, fy, fw, fh, confidence = face
            distance = abs(fx + fw / 2 - width / 2) / width
            return fw * fh * confidence * (1 - 0.25 * distance)

        fx, fy, fw, fh, _ = max(faces, key=score)
        x, y = fx + fw / 2 - w / 2, fy - h * 0.15
        for start, end, size, total, axis in (
            (max(0, fx - fw * 0.25), min(width, fx + fw * 1.25), w, width, "x"),
            (max(0, fy - fh * 0.35), min(height, fy + fh * 1.2), h, height, "y"),
        ):
            low, high = max(0, end - size), min(start, total - size)
            if low <= high:
                if axis == "x":
                    x = min(max(x, low), high)
                else:
                    y = min(max(y, low), high)
    return round(min(max(x, 0), width - w)), round(min(max(y, 0), height - h)), w, h


def crop_poster(data: bytes, box: tuple[int, int, int, int] | None = None, center: bool = False) -> dict:
    import cv2
    import numpy as np
    from PIL import ImageOps

    if len(data) > 32 * 1024 * 1024:
        raise ValueError("artwork exceeds 32 MiB")
    with Image.open(BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).copy()
        image_format = source.format
    width, height = image.size
    faces = []
    if box is None and not center:
        scale = min(1, 960 / max(width, height))
        frame = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2BGR)
        frame = cv2.resize(frame, (max(32, round(width * scale)), max(32, round(height * scale))))
        try:
            detector = cv2.FaceDetectorYN.create(
                str(Path(__file__).parent / "data/face_detection_yunet.onnx"),
                "",
                frame.shape[1::-1],
                0.75,
                0.3,
                5000,
            )
            _, detected = detector.detect(frame)
        except cv2.error as exc:
            raise ValueError("face detection failed; check the YuNet model") from exc
        if detected is not None:
            sx, sy = width / frame.shape[1], height / frame.shape[0]
            faces = [
                [
                    float(f[0] * sx),
                    float(f[1] * sy),
                    float(f[2] * sx),
                    float(f[3] * sy),
                    float(f[14]),
                ]
                for f in detected
                if f[2] > 0 and f[3] > 0
            ]
    box = box or crop_box(width, height, faces)
    x, y, w, h = box
    if min(x, y) < 0 or min(w, h) < 1 or x + w > width or y + h > height or w * 3 != h * 2:
        raise ValueError("crop must be an in-bounds 2:3 rectangle")
    cropped = image.crop((x, y, x + w, y + h))
    output = BytesIO()
    image_format = image_format if image_format in {"JPEG", "PNG", "WEBP"} else "PNG"
    cropped.save(
        output,
        format=image_format,
        **({"quality": 95} if image_format != "PNG" else {}),
    )
    preview = BytesIO()
    cropped.thumbnail((640, 960))
    cropped.save(preview, format="PNG")
    return {
        "content": output.getvalue(),
        "preview": preview.getvalue(),
        "box": box,
        "faces": faces,
        "width": width,
        "height": height,
        "extension": ".jpg" if image_format == "JPEG" else "." + image_format.lower(),
    }


def actor_image(output: Path, name: str) -> Path | None:
    folder = output.resolve() / "actors"
    if (
        not name
        or name in {".", ".."}
        or Path(name).name != name
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in name)
    ):
        raise ValueError("invalid actor name")
    if folder.is_symlink():
        raise ValueError("actor image directory must not be a symlink")
    return next(
        (
            p
            for p in folder.glob("*")
            if p.stem == name
            and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
            and p.is_file()
            and not p.is_symlink()
        ),
        None,
    )


class Gfriends:
    """One transient file tree per task; portraits remain ordinary metadata files."""

    base = "https://raw.githubusercontent.com/gfriends/gfriends/master/"

    def __init__(self, settings, names, output: Path, client=None):
        self.settings, self.names, self.output = settings, names, output
        self.client = client or httpx.AsyncClient(
            timeout=settings.timeout_seconds, follow_redirects=True, trust_env=False
        )
        self.owns_client = client is None
        self.index = None
        self.index_error = None
        self.last_request = 0.0
        self.completed = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if self.owns_client:
            await self.client.aclose()

    async def document(self, url: str, limit: int) -> bytes:
        for attempt in range(self.settings.retries + 1):
            await asyncio.sleep(max(0, 1 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                async with self.client.stream("GET", url) as response:
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > limit:
                            raise ValueError("gfriends response exceeds size limit")
                    return bytes(data)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 and exc.response.status_code != 429:
                    raise ValueError(f"gfriends HTTP {exc.response.status_code}") from None
            except httpx.RequestError:
                pass
            if attempt < self.settings.retries:
                await asyncio.sleep(self.settings.retry_delay_seconds)
        raise ValueError("gfriends request failed")

    def select(self, name: str) -> str | None:
        if comparison_name(name) in self.names.ambiguous:
            raise ValueError("actor name is ambiguous")
        primary = self.names.resolve(name) or name
        candidates = [
            primary,
            *[
                alias
                for alias in self.names.actors.get(primary, {}).get("aliases", [])
                if self.names.resolve(alias) == primary
            ],
        ]
        for candidate in dict.fromkeys(candidates):
            # Content is ordered low-to-high quality; the last bank wins.
            for bank, files in reversed(list(self.index.items())):
                if not isinstance(files, dict):
                    continue
                value = files.get(candidate + ".jpg")
                if not isinstance(value, str):
                    continue
                parsed = urlsplit(value)
                if (
                    parsed.scheme
                    or parsed.netloc
                    or Path(parsed.path).name != parsed.path
                    or Path(bank).name != bank
                    or bank in {".", ".."}
                ):
                    continue
                canonical = Path(parsed.path).stem.removeprefix("AI-Fix-")
                canonical = re.sub(r"-\d+$", "", canonical)
                if (
                    comparison_name(canonical) != comparison_name(primary)
                    and self.names.resolve(canonical) != primary
                ):
                    continue
                return (
                    self.base
                    + "Content/"
                    + quote(bank, safe="")
                    + "/"
                    + quote(parsed.path, safe="")
                    + ("?" + parsed.query if parsed.query else "")
                )
        return None

    async def save(self, actors, replace: bool = False) -> list[dict]:
        records = []
        for actor in actors:
            name = actor.name
            primary = self.names.resolve(name) or name
            if (primary, replace) in self.completed:
                records.append(self.completed[(primary, replace)])
                continue
            try:
                if comparison_name(name) in self.names.ambiguous:
                    raise ValueError("actor name is ambiguous")
                existing = actor_image(self.output, primary)
                if existing and not replace:
                    records.append({"name": primary, "status": "existing"})
                    self.completed[(primary, replace)] = records[-1]
                    continue
                report_progress("portraits", source="gfriends")
                if self.index is None and self.index_error is None:
                    try:
                        tree = json.loads(await self.document(self.base + "Filetree.json", 16 * 1024 * 1024))
                        if not isinstance(tree, dict):
                            raise ValueError("invalid gfriends file tree")
                        self.index = tree["Content"]
                        if not isinstance(self.index, dict):
                            raise ValueError("invalid gfriends file tree")
                    except (ValueError, KeyError) as exc:
                        self.index_error = str(exc)
                if self.index_error:
                    raise ValueError(self.index_error)
                url = self.select(name)
                if not url:
                    records.append({"name": primary, "status": "not_found"})
                    self.completed[(primary, replace)] = records[-1]
                    continue
                image = validate_image(await self.document(url, 32 * 1024 * 1024))
                extension = ".jpg" if image.format == "jpeg" else "." + image.format
                target = self.output / "actors" / (primary + extension)
                atomic_write(target, image.content)
                if existing and existing != target:
                    existing.unlink()
                records.append({"name": primary, "status": "saved"})
            except (ValueError, OSError) as exc:
                records.append({"name": primary, "status": "error", "error": str(exc)})
            self.completed[(primary, replace)] = records[-1]
            report_progress("portraits", records[-1]["status"], source="gfriends", message=primary)
        return records
