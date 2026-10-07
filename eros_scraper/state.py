from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from .storage import read_json, write_json


def now() -> str:
    return datetime.now(UTC).isoformat()


def state_directory(output: Path) -> Path:
    return Path("/data/state") / sha256(str(output.resolve()).encode()).hexdigest()[:16]


class State:
    """Small atomic JSON files. No database or redundant source metadata."""

    def __init__(self, root: Path):
        self.root = state_directory(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.movies_data = read_json(self.root / "scan.json", [])
        self.results = read_json(self.root / "results.json", {})
        self.issues = read_json(self.root / "actors-unknown.json", {})
        self.task_data = {p.stem: read_json(p, {}) for p in (self.root / "tasks").glob("*.json")}
        self.deleted = set()
        for task in list(self.task_data.values()):
            if task["status"] in ("pending", "running"):
                task.update(status="finished", error="process interrupted", finished_at=now())
                for item in task["items"]:
                    if item["status"] in ("pending", "running"):
                        item.update(status="failed", result={"error": "process interrupted"})
                self.save_task(task)

    def delete_task(self, ident):
        self.deleted.add(ident)
        self.task_data.pop(ident, None)
        (self.root / "tasks" / (ident + ".json")).unlink(missing_ok=True)

    def save_task(self, task):
        if task["id"] in self.deleted:
            return
        task["progress"] = sum(i["status"] in ("success", "failed") for i in task["items"])
        task["success"] = sum(i["status"] == "success" for i in task["items"])
        task["failed"] = sum(i["status"] == "failed" for i in task["items"])
        self.task_data[task["id"]] = task
        write_json(self.root / "tasks" / (task["id"] + ".json"), task)

    def task(self, task_id, include_items=True):
        task = self.task_data[task_id]
        return dict(task) if include_items else {k: v for k, v in task.items() if k != "items"}

    def tasks(self):
        return [
            self.task(t["id"], False)
            for t in sorted(self.task_data.values(), key=lambda t: t["created_at"], reverse=True)
        ]

    def movies(self, status=None):
        return [dict(m) for m in self.movies_data if status is None or m["status"] == status]

    def movie(self, key):
        from .scanner import Movie

        values = [m for m in self.movies_data if m["key"] == key]
        if len(values) != 1 or values[0]["status"] in ("invalid_key", "conflict"):
            raise ValueError("movie number is missing, invalid or conflicting")
        return Movie.model_validate(values[0])

    def save_scan(self, movies):
        data = [m.model_dump(mode="json") for m in movies]
        write_json(self.root / "scan.json", data)
        self.movies_data = data

    def record_result(self, key, result):
        results = {**self.results, key: result}
        write_json(self.root / "results.json", results)
        self.results = results
        for issue in result.get("actor_issues", []):
            old = self.issues.get(issue["name"], {})
            self.issues[issue["name"]] = {**issue, "count": old.get("count", 0) + 1, "last_seen_at": now()}
        write_json(self.root / "actors-unknown.json", self.issues)

    def actor_issues(self):
        return sorted(self.issues.values(), key=lambda v: (-v["count"], v["name"]))
