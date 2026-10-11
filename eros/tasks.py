import asyncio
import fcntl
import logging
import sqlite3
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .actors import ActorAliases
from .catalog import CatalogRules
from .engine import Scraper
from .images import Gfriends
from .logging import cleanup, configure, progress, report_progress, work_code
from .logging import task_id as log_task_id
from .models import Category, SourceId
from .nfo import existing_nfo, nfo_paths, read_nfo, serialize_nfo, write_nfo_files
from .processing import process_work, resolve_actor_names
from .runtime import RuntimeStore, now, runtime_directory
from .scanner import scan, validate_roots
from .settings import Settings
from .sources import source_for_url
from .translation import Translator

logger = logging.getLogger(__name__)


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal[
        "fetch_all",
        "fill_missing",
        "fetch_selected",
        "fetch_source",
        "refresh",
        "fetch_portraits",
    ]
    codes: list[str] = Field(default_factory=list)
    source_id: SourceId | None = None
    external_id: str | None = None
    source_url: str | None = None
    enrich: bool = True
    images: bool = True
    replace_portraits: bool = False
    actors: list[str] = Field(default_factory=list)


class TaskQueue:
    def __init__(self, settings: Settings, *, scraper_factory=Scraper):
        if not settings.media_directories or settings.metadata_directory is None:
            raise ValueError("media_directories and metadata_directory are required")
        validate_roots(settings.media_directories, settings.metadata_directory, require_existing=False)
        self.settings = settings
        actor_file = runtime_directory(settings.metadata_directory) / "actor_aliases.json"
        if settings.actor_aliases_file is None and actor_file.is_file():
            settings.actor_aliases_file = actor_file
        self.output = settings.metadata_directory.resolve()
        internal = runtime_directory(self.output)
        if internal.is_symlink():
            raise ValueError("runtime directory must not be a symlink")
        internal.mkdir(parents=True, exist_ok=True)
        lock = internal / "service.lock"
        if lock.is_symlink():
            raise ValueError("service lock must not be a symlink")
        self.process_lock = lock.open("a")
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.runtime = RuntimeStore(self.output)
        except OSError, ValueError, sqlite3.Error:
            self.process_lock.close()
            raise ValueError("runtime records are unreadable or another Eros writer is active") from None
        configure()
        self.running = {}
        self.previews = {}
        self.task_lock = asyncio.Lock()
        self.scraper_factory = scraper_factory
        self.translator = Translator(settings.translation)
        self.stop_event = asyncio.Event()

    def active(self):
        return [t for t in self.runtime.tasks() if t["status"] in ("pending", "running")]

    def _scan_rows(self, options, results, previous, persist):
        works = scan(options.media_directories, set(options.video_extensions))
        rules = CatalogRules.load(options.catalog_rules_file)
        for work in works:
            if work.status == "pending":
                work.category = Category(
                    results.get(work.code, {}).get("category", rules.classify(work.code).category)
                )
                result = results.get(work.code, {})
                failed = result.get("status") not in (
                    None,
                    "found",
                    "unchanged",
                ) or any(image.get("status") == "error" for image in result.get("images", []))
                path = existing_nfo(self.output, work)
                if failed:
                    work.status = "failed"
                if path.is_file():
                    try:
                        for nfo in nfo_paths(self.output, work):
                            read_nfo(nfo, work.code)
                        result = results.get(work.code, {})
                        work.status = (
                            "translation_partial"
                            if result.get("translation_errors")
                            else "failed"
                            if failed
                            else "scraped"
                        )
                    except ValueError, OSError:
                        work.warnings.append("NFO 无效，等待补抓")
            elif persist and (str(work.path), work.code, work.status) not in previous:
                logger.warning("跳过 %s：%s", work.code, "; ".join(work.warnings))
        return works

    def scan(self, *, settings=None, persist=True):
        if self.active():
            raise ValueError("cannot scan while a task is active")
        previous = {(m["path"], m["code"], m["status"]) for m in self.runtime.works()}
        works = self._scan_rows(settings or self.settings, self.runtime.results, previous, persist)
        if persist:
            self.runtime.save_scan(works)
        return [work.model_dump(mode="json") for work in works]

    async def scan_async(self, *, settings=None, persist=True):
        if self.active():
            raise ValueError("cannot scan while a task is active")
        options, results = settings or self.settings, self.runtime.results
        previous = {(m["path"], m["code"], m["status"]) for m in self.runtime.works()}
        works = await asyncio.to_thread(self._scan_rows, options, results, previous, persist)
        if self.active():
            raise ValueError("cannot scan while a task is active")
        # Do not publish an old filesystem snapshot after a write or configuration switch.
        if persist:
            if (
                self.stop_event.is_set()
                or options is not self.settings
                or results is not self.runtime.results
            ):
                return self.runtime.works()
            self.runtime.save_scan(works)
        return [work.model_dump(mode="json") for work in works]

    def create(self, request: TaskRequest):
        validate_roots(self.settings.media_directories, self.output)
        if request.type == "fetch_source" and request.source_id is None:
            raise ValueError("fetch_source requires source_id")
        if request.type == "fetch_selected" and not request.codes:
            raise ValueError("fetch_selected requires keys")
        eligible = {m["code"] for m in self.runtime.works() if m["status"] not in ("invalid_key", "conflict")}
        keys = sorted(set(request.codes) if request.codes else eligible)
        if not keys or any(k not in eligible for k in keys):
            raise ValueError("no eligible works or requested directory is invalid/conflicting")
        if (request.external_id or request.source_url) and (request.source_id is None or len(keys) != 1):
            raise ValueError("explicit work ID or URL requires one key and a source")
        if request.source_url and source_for_url(request.source_url, self.settings) != request.source_id:
            raise ValueError("作品链接与所选来源不一致")
        params = request.model_dump(mode="json")
        params["codes"] = keys
        for task in self.active():
            if task["params"] == params:
                return self.runtime.task(task["id"])
            if set(keys) & set(task["params"]["codes"]):
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
            "items": [{"code": key, "status": "pending", "attempts": 0, "result": None} for key in keys],
        }
        self.runtime.save_task(task)
        worker = asyncio.create_task(self.run(ident))
        self.running[ident] = worker
        worker.add_done_callback(lambda _: self.running.pop(ident, None))
        return self.runtime.task(ident)

    def delete(self, ident):
        task = self.runtime.task_data[ident]
        if task["status"] in ("pending", "running"):
            raise ValueError("cancel the task before deleting it")
        self.runtime.delete_task(ident)
        return {"deleted": True}

    async def cancel(self, ident):
        task = self.runtime.task_data[ident]
        if task["status"] not in ("pending", "running"):
            raise ValueError("task is not active")
        worker = self.running.get(ident)
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        for item in task["items"]:
            if item["status"] in ("pending", "running"):
                item.update(status="cancelled", result=None)
                event = {
                    "stage": "finished",
                    "status": "cancelled",
                    "source": None,
                    "message": "任务已取消",
                    "time": now(),
                }
                item["events"] = [*item.get("events", []), event][-60:]
                item["stage"] = event
            self.previews.pop(item["code"], None)
        task.update(status="cancelled", finished_at=now(), current=None)
        self.runtime.save_task(task)
        active_keys = {key for other in self.active() for key in other["params"]["codes"]}
        refreshed = self._scan_rows(self.settings, self.runtime.results, set(), False)
        by_path = {str(work.path): work.model_dump(mode="json") for work in refreshed}
        self.runtime.save_catalog(
            [
                by_path.get(row["path"], row) if row["code"] not in active_keys else row
                for row in self.runtime.works_data
            ]
        )
        return self.runtime.task(ident)

    def retry(self, ident):
        task = self.runtime.task(ident)
        if task["status"] in ("running", "pending"):
            raise ValueError("cannot retry active task")
        keys = [i["code"] for i in task["items"] if i["status"] in ("failed", "cancelled")]
        if not keys:
            raise ValueError("task contains no failed items")
        return self.create(TaskRequest.model_validate({**task["params"], "codes": keys}))

    async def run(self, ident):
        async with self.task_lock:
            try:
                if ident not in self.runtime.deleted:
                    await self._run(ident)
            finally:
                for key in self.runtime.task_data.get(ident, {}).get("params", {}).get("codes", []):
                    self.previews.pop(key, None)

    async def _run(self, ident):
        log_task_id.set(ident)
        task = self.runtime.task_data[ident]
        request = TaskRequest.model_validate(task["params"])
        task["status"] = "running"
        self.runtime.save_task(task)
        try:
            names = ActorAliases.load(self.settings.actor_aliases_file)
            pending = names.pending(self.runtime.issues)
            if pending != self.runtime.issues:
                self.runtime.save_issues(pending)
            async with (
                self.scraper_factory(self.settings) as scraper,
                Gfriends(self.settings, names, self.output) as portraits,
            ):

                async def one(item):
                    work_code.set(item["code"])

                    def update_progress(event):
                        if preview := event.pop("metadata", None):
                            self.previews[item["code"]] = preview
                        previous = item.get("stage")
                        if previous and all(
                            previous.get(key) == event.get(key)
                            for key in ("stage", "status", "source", "message")
                        ):
                            return
                        item["events"] = [*item.get("events", []), event][-60:]
                        item["stage"] = event
                        task["current"] = {"code": item["code"], **event}
                        self.runtime.save_task(task, item)

                    token = progress.set(update_progress)
                    report = {}
                    success = False
                    for attempt in range(self.settings.item_retries + 1):
                        item.update(status="running", attempts=item["attempts"] + 1)
                        for row in self.runtime.works_data:
                            if row["code"] == item["code"] and request.type != "fetch_portraits":
                                row["status"] = "running"
                        self.runtime.save_catalog(self.runtime.works_data)
                        self.runtime.save_task(task, item)
                        try:
                            work = self.runtime.work(item["code"])
                            report = await self.process(scraper, work, names, request, portraits)
                            success = (
                                report["status"] in ("found", "unchanged")
                                and not report.get("translation_errors")
                                and not any(i.get("status") == "error" for i in report.get("images", []))
                            )
                        except Exception as exc:
                            logger.exception("处理 %s 失败", item["code"])
                            report = {
                                "code": item["code"],
                                "status": "error",
                                "error": str(exc),
                            }
                        if (
                            report.get("status") not in ("network_error", "error")
                            or attempt == self.settings.item_retries
                        ):
                            break
                        report_progress("retry")
                        await asyncio.sleep(self.settings.retry_delay_seconds)
                    report_progress("finished", "success" if success else "failed")
                    progress.reset(token)
                    item.update(status="success" if success else "failed", result=report)
                    self.previews.pop(item["code"], None)
                    if request.type != "fetch_portraits":
                        self.runtime.record_result(item["code"], report)
                    self.runtime.save_task(task, item)
                    for row in self.runtime.works_data:
                        if row["code"] == item["code"] and request.type != "fetch_portraits":
                            row["status"] = (
                                "translation_partial"
                                if report.get("translation_errors")
                                else "scraped"
                                if success
                                else "failed"
                            )
                    self.runtime.save_catalog(self.runtime.works_data)
                    logger.info("番号 %s：%s", item["code"], report.get("status"))

                for item in task["items"]:
                    if ident in self.runtime.deleted:
                        break
                    if self.stop_event.is_set():
                        item.update(status="failed", result={"error": "process interrupted"})
                        continue
                    await one(item)
            task.update(
                status="finished",
                finished_at=now(),
            )
            self.runtime.save_task(task)
        except Exception as exc:
            for item in task["items"]:
                if item["status"] in ("pending", "running"):
                    item.update(status="failed", result={"error": str(exc)})
            task.update(status="finished", error=str(exc), finished_at=now())
            self.runtime.save_task(task)

    async def process(self, scraper, work, names, request, portraits):
        path = existing_nfo(self.output, work)
        if request.type == "fetch_portraits":
            metadata = read_nfo(path, work.code)
            actors = [
                actor for actor in metadata.actors if not request.actors or actor.name in request.actors
            ]
            images = await portraits.save(actors, replace=request.replace_portraits)
            return {"code": work.code, "status": "found", "images": images}
        if request.type == "fill_missing" and path.is_file() and not request.images:
            report_progress("metadata")
            metadata = read_nfo(path, work.code)
            issues = resolve_actor_names(metadata, names)
            if self.settings.translation.enabled:
                report_progress("translation")
            errors = await self.translator.metadata(metadata)
            content = serialize_nfo(metadata)
            write_nfo_files(self.output, work, content)
            return {
                "code": work.code,
                "status": "found",
                "actor_issues": issues,
                "translation_errors": errors,
                "portraits": await portraits.save(metadata.actors)
                if self.settings.download_portraits
                else [],
            }
        return await process_work(
            scraper,
            work,
            self.output,
            names,
            self.translator,
            source=request.source_id,
            source_url=request.source_url,
            external_id=request.external_id,
            enrich=request.enrich,
            images=request.images,
            missing_only=request.type == "fill_missing",
            portraits=portraits if self.settings.download_portraits else None,
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
        self.runtime.close()
        self.process_lock.close()
        cleanup()
