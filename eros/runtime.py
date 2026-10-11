import json
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat()


def runtime_directory(output: Path) -> Path:
    return Path("/data/runtime") / sha256(str(output.resolve()).encode()).hexdigest()[:16]


class RuntimeStore:
    """Runtime records in SQLite; NFO, images and maintained mappings stay files."""

    def __init__(self, root: Path):
        self.root = runtime_directory(root)
        if self.root.is_symlink():
            raise ValueError("runtime directory must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        database = self.root / "runtime.sqlite3"
        for path in (database, Path(str(database) + "-wal"), Path(str(database) + "-shm")):
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError("runtime database must be a regular file")
        self.connection = sqlite3.connect(database, timeout=5)
        try:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(kind, id))"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS task_items (task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE, "
                "code TEXT NOT NULL, position INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY(task_id, code))"
            )
            self.works_data = self._read("scan").get("catalog", [])
            self.results = self._read("result")
            self.issues = self._read("actor")
            self.task_data = {
                ident: {"sequence": sequence, **json.loads(data), "items": []}
                for sequence, ident, data in self.connection.execute("SELECT rowid, id, data FROM tasks")
            }
            self.task_sequence = max(
                self._read("counter").get("task", 0),
                max((task["sequence"] for task in self.task_data.values()), default=0),
            )
            for ident, data in self.connection.execute(
                "SELECT task_id, data FROM task_items ORDER BY position"
            ):
                self.task_data[ident]["items"].append(json.loads(data))
            self.deleted = set()
            for task in list(self.task_data.values()):
                if task["status"] in ("pending", "running"):
                    task.update(status="finished", error="process interrupted", finished_at=now())
                    for item in task["items"]:
                        if item["status"] in ("pending", "running"):
                            item.update(status="failed", result={"error": "process interrupted"})
                    self.save_task(task)
        except Exception:
            self.connection.close()
            raise

    def _read(self, kind):
        return {
            key: json.loads(data)
            for key, data in self.connection.execute("SELECT id, data FROM records WHERE kind = ?", (kind,))
        }

    def _write(self, kind, key, value):
        self.connection.execute(
            "INSERT INTO records VALUES (?, ?, ?) ON CONFLICT(kind, id) DO UPDATE SET data=excluded.data",
            (kind, key, json.dumps(value, ensure_ascii=False)),
        )

    def has_scan(self):
        return (
            self.connection.execute("SELECT 1 FROM records WHERE kind='scan' AND id='catalog'").fetchone()
            is not None
        )

    def save_catalog(self, data):
        with self.connection:
            self._write("scan", "catalog", data)
        self.works_data = data

    def clear_scan(self):
        with self.connection:
            self.connection.execute("DELETE FROM records WHERE kind='scan'")
        self.works_data = []

    def save_issues(self, issues):
        with self.connection:
            self.connection.execute("DELETE FROM records WHERE kind='actor'")
            for name, issue in issues.items():
                self._write("actor", name, issue)
        self.issues = issues

    def close(self):
        self.connection.close()

    def delete_task(self, ident):
        with self.connection:
            self.connection.execute("DELETE FROM tasks WHERE id=?", (ident,))
        self.deleted.add(ident)
        self.task_data.pop(ident, None)

    def save_task(self, task, item=None):
        if task["id"] in self.deleted:
            return
        task["progress"] = sum(
            i["status"] in ("success", "failed", "cancelled", "skipped") for i in task["items"]
        )
        task["success"] = sum(i["status"] == "success" for i in task["items"])
        task["failed"] = sum(i["status"] == "failed" for i in task["items"])
        task["skipped"] = sum(i["status"] in ("skipped", "cancelled") for i in task["items"])
        with self.connection:
            if task["id"] not in self.task_data:
                task["sequence"] = self.task_sequence + 1
                self._write("counter", "task", task["sequence"])
            header = {key: value for key, value in task.items() if key != "items"}
            self.connection.execute(
                "INSERT INTO tasks VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (task["id"], json.dumps(header, ensure_ascii=False)),
            )
            for position, entry in enumerate(task["items"]):
                if item is not None and entry is not item:
                    continue
                self.connection.execute(
                    "INSERT INTO task_items VALUES (?, ?, ?, ?) ON CONFLICT(task_id, code) DO UPDATE SET data=excluded.data",
                    (task["id"], entry["code"], position, json.dumps(entry, ensure_ascii=False)),
                )
        self.task_data[task["id"]] = task
        self.task_sequence = max(self.task_sequence, task["sequence"])

    def task(self, task_id, include_items=True):
        task = self.task_data[task_id]
        return dict(task) if include_items else {k: v for k, v in task.items() if k != "items"}

    def tasks(self):
        return [
            self.task(t["id"], False)
            for t in sorted(self.task_data.values(), key=lambda t: t["created_at"], reverse=True)
        ]

    def works(self, status=None):
        return [dict(m) for m in self.works_data if status is None or m["status"] == status]

    def work(self, code):
        from .scanner import Work

        values = [m for m in self.works_data if m["code"] == code]
        if len(values) != 1 or values[0]["status"] in ("invalid_key", "conflict"):
            raise ValueError("work code is missing, invalid or conflicting")
        return Work.model_validate(values[0])

    def save_scan(self, works):
        data = [m.model_dump(mode="json") for m in works]
        self.save_catalog(data)

    def record_result(self, key, result):
        issues = dict(self.issues)
        for issue in result.get("actor_issues", []):
            old = issues.get(issue["name"], {})
            issues[issue["name"]] = {**issue, "count": old.get("count", 0) + 1, "last_seen_at": now()}
        with self.connection:
            self._write("result", key, result)
            for issue in result.get("actor_issues", []):
                self._write("actor", issue["name"], issues[issue["name"]])
        self.results = {**self.results, key: result}
        self.issues = issues
