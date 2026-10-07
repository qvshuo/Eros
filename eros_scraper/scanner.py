from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, Field

from .models import Category
from .numbers import normalize_number

EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".ts", ".strm"}


class Movie(BaseModel):
    key: str
    path: Path
    media_files: list[Path]
    number: str | None = None
    status: str = "pending"
    category: Category = Category.UNKNOWN
    warnings: list[str] = Field(default_factory=list)


def validate_key(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or any(c in value for c in '/\\:*?"<>|')
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("目录名必须是标准番号，请手动修改")
    if normalize_number(value) != value:
        raise ValueError("目录名必须是标准番号，Eros 不会修正名称")
    return value


def validate_roots(roots: list[Path], output: Path, *, require_existing: bool = True) -> None:
    output = output.resolve()
    for root in roots:
        if require_existing and not root.is_dir():
            raise ValueError(f"media root is not a directory: {root}")
        root = root.resolve()
        if output == root or output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError("media roots and metadata root must be separate directory trees")


def scan(roots: list[Path], extensions: set[str] | None = None) -> list[Movie]:
    folders: dict[Path, set[Path]] = defaultdict(set)
    for root in roots:
        if not root.is_dir():
            raise ValueError(f"media root is not a directory: {root}")

        def error(exc):
            raise exc

        for directory, dirs, filenames in os.walk(root.resolve(), followlinks=False, onerror=error):
            dirs[:] = sorted(
                d
                for d in dirs
                if not d.startswith(".")
                and d not in {"@eaDir", "#recycle"}
                and not (Path(directory) / d).is_symlink()
            )
            for name in filenames:
                path = Path(directory) / name
                if (
                    not name.startswith(".")
                    and path.suffix.lower() in (extensions or EXTENSIONS)
                    and not path.is_symlink()
                ):
                    folders[path.parent].add(path)
    movies = []
    for folder, paths in sorted(folders.items()):
        movie = Movie(key=folder.name, path=folder, media_files=sorted(paths))
        try:
            movie.number = validate_key(movie.key)
        except ValueError:
            movie.status = "invalid_key"
            movie.warnings.append("请手动修正目录名")
        movies.append(movie)
    keys = defaultdict(list)
    for movie in movies:
        keys[movie.key.casefold()].append(movie)
    for group in keys.values():
        if len(group) > 1:
            for movie in group:
                movie.status = "conflict"
                movie.warnings.append("多个目录使用相同番号")
    return movies
