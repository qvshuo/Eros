"""Japanese primary names with conservative alias lookup; no online actor scraping."""

from __future__ import annotations

import json
import re
import unicodedata
from importlib.resources import files
from pathlib import Path


def comparison_name(name: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", name).strip()).casefold()


class ActorNames:
    def __init__(self, actors: dict[str, dict]):
        if not isinstance(actors, dict):
            raise ValueError("actor mapping must contain an actors object")
        for name, record in actors.items():
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(record, dict)
                or any(
                    not isinstance(record.get(field), list)
                    or any(not isinstance(v, str) for v in record[field])
                    for field in ("aliases", "homepages")
                )
            ):
                raise ValueError("actor mapping requires primary names, aliases and homepages string lists")
        self.actors = actors
        owners: dict[str, set[str]] = {}
        for name, record in actors.items():
            for alias in [name, *record["aliases"]]:
                owners.setdefault(comparison_name(alias), set()).add(name)
        self.aliases = {alias: next(iter(names)) for alias, names in owners.items() if len(names) == 1}
        self.ambiguous = {alias: sorted(names) for alias, names in owners.items() if len(names) > 1}

    @classmethod
    def load(cls, path: Path | None = None) -> ActorNames:
        value = (
            path.read_text(encoding="utf-8")
            if path
            else files("eros_scraper").joinpath("data/actor_names.json").read_text(encoding="utf-8")
        )
        document = json.loads(value)
        if not isinstance(document, dict) or document.get("version") != 1 or "actors" not in document:
            raise ValueError("unsupported actor mapping schema")
        return cls(document["actors"])

    def resolve(self, name: str) -> str | None:
        """Return a Japanese primary name only when identity is unambiguous."""
        key = comparison_name(name)
        if key in self.ambiguous:
            return None
        if key in self.aliases:
            return self.aliases[key]
        if match := re.fullmatch(r"(.+?)\(([^()]+)\)", key):
            parts = [comparison_name(part) for part in match.groups()]
            if all(part in self.aliases for part in parts):
                candidates = {self.aliases[part] for part in parts}
                if len(candidates) == 1:
                    return next(iter(candidates))
                self.ambiguous[key] = sorted(candidates)
        return None

    def pending(self, issues: dict[str, dict]) -> dict[str, dict]:
        pending = {}
        for name, issue in issues.items():
            if self.resolve(name) is None:
                candidates = self.ambiguous.get(comparison_name(name), [])
                pending[name] = {
                    **issue,
                    "status": "ambiguous" if candidates else "unknown",
                    "candidates": candidates,
                }
        return pending
