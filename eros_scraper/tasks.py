import asyncio
import fcntl
import logging
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .actor_names import ActorNames
from .config import Settings
from .engine import Scraper
from .library import normalize_actors, scrape_movie
from .logging_setup import cleanup, configure, movie_key
from .logging_setup import task_id as log_task_id
from .models import Category, SourceId
from .numbers import NumberRules
from .scanner import scan, validate_roots
from .sources import source_for_url
from .state import State, now, state_directory
from .storage import write_json
from .translation import Translator
from .writer import existing_nfo, nfo_bytes, nfo_paths, read_nfo, write_nfos

logger = logging.getLogger(__name__)


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["scrape_full", "scrape_missing", "scrape_keys", "scrape_source", "force_update"]
    keys: list[str] = Field(default_factory=list)
    source_id: SourceId | None = None
    external_id: str | None = None
    source_url: str | None = None
    enrich: bool = True
    images: bool = True


class TaskRunner:
    def __init__(self, settings: Settings, *, scraper_factory=Scraper):
        if not settings.media_roots or settings.metadata_root is None:
            raise ValueError("media_roots and metadata_root are required")
        validate_roots(settings.media_roots, settings.metadata_root, require_existing=False)
        self.settings = settings
        actor_file = state_directory(settings.metadata_root) / "actor_names.json"
        if settings.actor_names_file is None and actor_file.is_file():
            settings.actor_names_file = actor_file
        self.output = settings.metadata_root.resolve()
        internal = state_directory(self.output)
        if internal.is_symlink():
            raise ValueError("state directory must not be a symlink")
        internal.mkdir(parents=True, exist_ok=True)
        self.process_lock = (internal / "service.lock").open("a")
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.state = State(self.output)
        except OSError, ValueError:
            self.process_lock.close()
            raise ValueError("state files are unreadable or another Eros writer is active") from None
        configure()
        self.running = {}
        self.hidden_tasks = {}
        self.task_lock = asyncio.Lock()
        self.scraper_factory = scraper_factory
        self.translator = Translator(settings.translation)
        self.stop_event = asyncio.Event()

    def active(self):
        return [
            t
            for t in [*self.state.tasks(), *self.hidden_tasks.values()]
            if t["status"] in ("pending", "running")
        ]

    def _scan_rows(self, options, results, previous, persist):
        movies = scan(options.media_roots, set(options.video_extensions))
        rules = NumberRules.load(options.rules_file)
        for movie in movies:
            if movie.status == "pending":
                movie.category = Category(
                    results.get(movie.key, {}).get("category", rules.classify(movie.key).category)
                )
                result = results.get(movie.key, {})
                failed = result.get("status") not in (None, "found", "unchanged") or any(
                    image.get("status") == "error" for image in result.get("images", [])
                )
                path = existing_nfo(self.output, movie)
                if failed:
                    movie.status = "failed"
                if path.is_file():
                    try:
                        for nfo in nfo_paths(self.output, movie):
                            read_nfo(nfo, movie.key)
                        result = results.get(movie.key, {})
                        movie.status = (
                            "translation_partial"
                            if result.get("translation_errors")
                            else "failed"
                            if failed
                            else "scraped"
                        )
                    except ValueError, OSError:
                        movie.warnings.append("NFO 无效，等待补抓")
            elif persist and (str(movie.path), movie.key, movie.status) not in previous:
                logger.warning("跳过 %s：%s", movie.key, "; ".join(movie.warnings))
        return movies

    def scan(self, *, settings=None, persist=True):
        if self.active():
            raise ValueError("cannot scan while a task is active")
        previous = {(m["path"], m["key"], m["status"]) for m in self.state.movies()}
        movies = self._scan_rows(settings or self.settings, self.state.results, previous, persist)
        if persist:
            self.state.save_scan(movies)
        return [movie.model_dump(mode="json") for movie in movies]

    async def scan_async(self, *, settings=None, persist=True):
        if self.active():
            raise ValueError("cannot scan while a task is active")
        options, results = settings or self.settings, self.state.results
        previous = {(m["path"], m["key"], m["status"]) for m in self.state.movies()}
        movies = await asyncio.to_thread(self._scan_rows, options, results, previous, persist)
        if self.active():
            raise ValueError("cannot scan while a task is active")
        # Do not publish an old filesystem snapshot after a write or configuration switch.
        if persist:
            if self.stop_event.is_set() or options is not self.settings or results is not self.state.results:
                return self.state.movies()
            self.state.save_scan(movies)
        return [movie.model_dump(mode="json") for movie in movies]

    def create(self, request: TaskRequest):
        validate_roots(self.settings.media_roots, self.output)
        if request.type == "scrape_source" and request.source_id is None:
            raise ValueError("scrape_source requires source_id")
        if request.type == "scrape_keys" and not request.keys:
            raise ValueError("scrape_keys requires keys")
        eligible = {m["key"] for m in self.state.movies() if m["status"] not in ("invalid_key", "conflict")}
        keys = sorted(set(request.keys) if request.keys else eligible)
        if not keys or any(k not in eligible for k in keys):
            raise ValueError("no eligible movies or requested directory is invalid/conflicting")
        if (request.external_id or request.source_url) and (request.source_id is None or len(keys) != 1):
            raise ValueError("explicit movie ID or URL requires one key and a source")
        if request.source_url and source_for_url(request.source_url, self.settings) != request.source_id:
            raise ValueError("作品链接与所选来源不一致")
        params = request.model_dump(mode="json")
        params["keys"] = keys
        for task in self.active():
            if task["params"] == params:
                return self.state.task(task["id"])
            if set(keys) & set(task["params"]["keys"]):
                raise ValueError("task overlaps an active write task")
        ident = uuid.uuid4().hex
        task = {
            "id": ident,
            "type": request.type,
            "status": "pending",
            "params": params,
            "total": len(keys),
            "progress": 0,
            "success": 0,
            "failed": 0,
            "created_at": now(),
            "finished_at": None,
            "error": None,
            "items": [{"key": key, "status": "pending", "attempts": 0, "result": None} for key in keys],
        }
        self.state.save_task(task)
        worker = asyncio.create_task(self.run(ident))
        self.running[ident] = worker
        worker.add_done_callback(lambda _: self.running.pop(ident, None))
        return self.state.task(ident)

    def delete(self, ident):
        task = self.state.task_data[ident]
        if task["status"] in ("pending", "running"):
            self.hidden_tasks[ident] = task
        self.state.delete_task(ident)
        return {"deleted": True}

    def retry(self, ident):
        task = self.state.task(ident)
        if task["status"] in ("running", "pending"):
            raise ValueError("cannot retry active task")
        keys = [i["key"] for i in task["items"] if i["status"] == "failed"]
        if not keys:
            raise ValueError("task contains no failed items")
        return self.create(TaskRequest.model_validate({**task["params"], "keys": keys}))

    async def run(self, ident):
        async with self.task_lock:
            try:
                if ident not in self.state.deleted:
                    await self._run(ident)
            finally:
                self.hidden_tasks.pop(ident, None)

    async def _run(self, ident):
        log_task_id.set(ident)
        task = self.state.task_data[ident]
        request = TaskRequest.model_validate(task["params"])
        task["status"] = "running"
        self.state.save_task(task)
        try:
            names = ActorNames.load(self.settings.actor_names_file)
            pending = names.pending(self.state.issues)
            if pending != self.state.issues:
                write_json(self.state.root / "actors-unknown.json", pending)
                self.state.issues = pending
            async with self.scraper_factory(self.settings) as scraper:

                async def one(item):
                    movie_key.set(item["key"])
                    report = {}
                    success = False
                    for attempt in range(self.settings.item_retries + 1):
                        item.update(status="running", attempts=item["attempts"] + 1)
                        for row in self.state.movies_data:
                            if row["key"] == item["key"]:
                                row["status"] = "running"
                        write_json(self.state.root / "scan.json", self.state.movies_data)
                        self.state.save_task(task)
                        try:
                            movie = self.state.movie(item["key"])
                            report = await self.process(scraper, movie, names, request)
                            success = (
                                report["status"] in ("found", "unchanged")
                                and not report.get("translation_errors")
                                and not any(i.get("status") == "error" for i in report.get("images", []))
                            )
                        except Exception as exc:
                            logger.exception("处理 %s 失败", item["key"])
                            report = {"key": item["key"], "status": "error", "error": str(exc)}
                        if (
                            report.get("status") not in ("network_error", "error")
                            or attempt == self.settings.item_retries
                        ):
                            break
                        await asyncio.sleep(self.settings.retry_delay_seconds)
                    item.update(status="success" if success else "failed", result=report)
                    self.state.record_result(item["key"], report)
                    self.state.save_task(task)
                    for row in self.state.movies_data:
                        if row["key"] == item["key"]:
                            row["status"] = (
                                "translation_partial"
                                if report.get("translation_errors")
                                else "scraped"
                                if success
                                else "failed"
                            )
                    write_json(self.state.root / "scan.json", self.state.movies_data)
                    logger.info("番号 %s：%s", item["key"], report.get("status"))

                for item in task["items"]:
                    if ident in self.state.deleted:
                        break
                    if self.stop_event.is_set():
                        item.update(status="failed", result={"error": "process interrupted"})
                        continue
                    await one(item)
            task.update(
                status="finished",
                finished_at=now(),
            )
            self.state.save_task(task)
        except Exception as exc:
            for item in task["items"]:
                if item["status"] in ("pending", "running"):
                    item.update(status="failed", result={"error": str(exc)})
            task.update(status="finished", error=str(exc), finished_at=now())
            self.state.save_task(task)

    async def process(self, scraper, movie, names, request):
        path = existing_nfo(self.output, movie)
        if request.type == "scrape_missing" and path.is_file() and not request.images:
            metadata = read_nfo(path, movie.key)
            issues = normalize_actors(metadata, names)
            errors = await self.translator.metadata(metadata)
            content = nfo_bytes(metadata)
            write_nfos(self.output, movie, content)
            return {"key": movie.key, "status": "found", "actor_issues": issues, "translation_errors": errors}
        return await scrape_movie(
            scraper,
            movie,
            self.output,
            names,
            self.translator,
            source=request.source_id,
            source_url=request.source_url,
            external_id=request.external_id,
            enrich=request.enrich,
            images=request.images,
            missing_only=request.type == "scrape_missing",
        )

    async def watch(self):
        while not self.stop_event.is_set():
            try:
                if not self.active():
                    await self.scan_async()
            except Exception:
                logger.exception("媒体监控扫描失败，保留上次清单")
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.settings.watch_interval_seconds)
            except TimeoutError:
                pass

    async def close(self):
        self.stop_event.set()
        if self.running:
            await asyncio.gather(*list(self.running.values()), return_exceptions=True)
        await self.translator.close()
        self.process_lock.close()
        cleanup()
