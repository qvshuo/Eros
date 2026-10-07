from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .models import Actor, Metadata
from .scanner import Movie, validate_key
from .storage import atomic_write


def media_stems(movie: Movie) -> list[str]:
    return sorted({path.stem for path in movie.media_files})


def nfo_paths(output: Path, movie: Movie) -> list[Path]:
    return [target_folder(output, movie.key) / (stem + ".nfo") for stem in media_stems(movie)]


def existing_nfo(output: Path, movie: Movie) -> Path:
    return next((p for p in nfo_paths(output, movie) if p.is_file()), nfo_paths(output, movie)[0])


def write_nfos(output: Path, movie: Movie, content: bytes) -> None:
    for path in nfo_paths(output, movie):
        if not path.is_file() or path.read_bytes() != content:
            atomic_write(path, content)


def xml_text(value: object) -> str:
    return "".join(
        c
        for c in str(value)
        if c in "\t\n\r"
        or 0x20 <= ord(c) <= 0xD7FF
        or 0xE000 <= ord(c) <= 0xFFFD
        or 0x10000 <= ord(c) <= 0x10FFFF
    )


def nfo_bytes(metadata: Metadata) -> bytes:
    root = ET.Element("movie")

    def add(name: str, value: object) -> None:
        if value is not None and str(value).strip():
            ET.SubElement(root, name).text = xml_text(value)

    add("title", metadata.title)
    add("originaltitle", metadata.original_title or metadata.title)
    if metadata.number:
        ET.SubElement(root, "uniqueid", {"type": "Eros", "default": "true"}).text = metadata.number
    if metadata.release_date:
        add("year", metadata.release_date.year)
        add("premiered", metadata.release_date.isoformat())
    for name, value in (
        ("plot", metadata.plot),
        ("runtime", metadata.runtime_minutes),
        ("studio", metadata.studio),
        ("publisher", metadata.publisher),
    ):
        add(name, value)
    if metadata.label != metadata.publisher:
        add("label", metadata.label)
    if metadata.series:
        ET.SubElement(ET.SubElement(root, "set"), "name").text = xml_text(metadata.series)
    for director in metadata.directors:
        add("director", director)
    for actor in metadata.actors:
        if actor.name.strip():
            ET.SubElement(ET.SubElement(root, "actor"), "name").text = xml_text(actor.name)
    for tag in metadata.tags:
        add("genre", tag)
        add("tag", tag)
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def target_folder(output: Path, key: str) -> Path:
    validate_key(key)
    output = output.resolve()
    target = output / key
    if target.is_symlink() or not target.resolve().is_relative_to(output):
        raise ValueError("metadata target escapes output root")
    return target


def read_nfo(path: Path, number: str) -> Metadata:
    if path.is_symlink():
        raise ValueError("NFO must not be a symlink")
    data = path.read_bytes()
    if len(data) > 4 * 1024 * 1024 or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("unsafe or oversized NFO")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ValueError("invalid movie NFO") from None
    if root.tag != "movie" or not root.findtext("title"):
        raise ValueError("invalid movie NFO")
    identifiers = [node.text for node in root.findall("uniqueid") if node.get("type") == "Eros"]
    if not identifiers or any(not value or value.strip() != number for value in identifiers):
        raise ValueError("NFO number differs from directory")
    return Metadata(
        number=number,
        title=root.findtext("title"),
        original_title=root.findtext("originaltitle"),
        plot=root.findtext("plot"),
        release_date=root.findtext("premiered") or None,
        runtime_minutes=root.findtext("runtime") or None,
        studio=root.findtext("studio"),
        publisher=root.findtext("publisher"),
        label=root.findtext("label"),
        series=root.findtext("set/name"),
        directors=[e.text for e in root.findall("director") if e.text],
        actors=[Actor(name=e.findtext("name")) for e in root.findall("actor") if e.findtext("name")],
        tags=[e.text for e in root.findall("tag") if e.text],
        source_url="",
    )
