"""Durable, explicit H3 768P -> 2K jobs. Never retry an uncertain submission."""
import asyncio
import contextlib
import hashlib
import json
import os
import sqlite3
import time
import uuid

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field


class UpgradeRequest(BaseModel):
    provider_id: str = Field(min_length=1, max_length=200)
    source_task_id: str = Field(min_length=1, max_length=240)
    confirmed: bool = False
    source: dict = Field(default_factory=dict)
    retry: bool = False


class MiniMaxVideoService:
    def __init__(self, module):
        self.m = module
        self.path = os.path.join(module.DATA_DIR, "minimax_video.sqlite3")
        self.active = {}

    @contextlib.contextmanager
    def db(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with contextlib.closing(sqlite3.connect(self.path, timeout=20)) as db:
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, source_key TEXT UNIQUE, payload TEXT NOT NULL)")
            with db:
                yield db

    def identity(self, provider_id, source_id):
        provider = self.m.get_api_provider(provider_id)
        if not self.m.is_apimart_provider(provider):
            raise HTTPException(400, "升至 2K 仅适用于 APIMart MiniMax-H3。")
        key = self.m.provider_env_key_value(provider_id)
        if not key:
            raise HTTPException(400, "请先配置原任务所属账号的 API Key。")
        # Bind jobs to an account without persisting credentials or exposing fingerprints.
        identity = hashlib.sha256((provider_id + "\0" + key + "\0" + source_id).encode()).hexdigest()
        return provider, identity

    def remember(self, summary):
        if summary.get("model") not in ("MiniMax-H3", "MiniMax-H3-Max") or not summary.get("task_id"):
            return
        _, identity = self.identity(summary["provider_id"], summary["task_id"])
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO sources VALUES (?, ?)", (identity, json.dumps(summary)))

    def read(self, job_id):
        with self.db() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "未找到升级任务。")
        return json.loads(row[0])

    def source(self, provider_id, task_id):
        _, identity = self.identity(provider_id, task_id)
        with self.db() as db:
            row = db.execute("SELECT payload FROM sources WHERE id=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def write(self, job, **changes):
        job.update(changes, updated_at=time.time())
        with self.db() as db:
            db.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(job), job["id"]))
        self.m.CANVAS_TASKS["h3-upgrade:" + job["id"]] = {
            "status": "running" if job["status"] in {"queued", "verifying", "submitting", "generating", "downloading", "waiting"} else job["status"],
            "type": "minimax-regeneration",
        }
        return job

    @staticmethod
    def public(job):
        return {k: v for k, v in job.items() if k not in {"source_key", "source"}}

    def authorized(self, job):
        provider, identity = self.identity(job["provider_id"], job["source_task_id"])
        if identity != job["source_key"]:
            raise HTTPException(409, "API 账号已改变，请切回原任务所属账号后查询。")
        return provider

    async def start(self, request):
        if not request.confirmed:
            raise HTTPException(400, "升至 2K 会额外计费，请先确认。")
        _, identity = self.identity(request.provider_id, request.source_task_id)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM jobs WHERE source_key=?", (identity,)).fetchone()
            if row:
                job = json.loads(row[0])
                if request.retry and job["status"] == "failed" and job.get("retryable"):
                    job.update(status="queued", upstream_task_id="", error="", retryable=False)
                    db.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(job), job["id"]))
            else:
                source = db.execute("SELECT payload FROM sources WHERE id=?", (identity,)).fetchone()
                source = json.loads(source[0]) if source else request.source
                if source.get("model") != "MiniMax-H3" or str(source.get("resolution", "")).upper() != "768P":
                    raise HTTPException(400, "仅成功的 MiniMax-H3 768P 结果可升至 2K，H3-Max 和外部视频不支持。")
                job = dict(id=uuid.uuid4().hex, source_key=identity, provider_id=request.provider_id,
                           source_task_id=request.source_task_id, source=source, status="queued",
                           upstream_task_id="", error="", retryable=False, created_at=time.time())
                db.execute("INSERT INTO jobs VALUES (?, ?, ?)", (job["id"], identity, json.dumps(job)))
        self.launch(job)
        return self.public(job)

    def launch(self, job):
        if job["id"] in self.active or job["status"] in {"completed", "failed", "uncertain"}:
            return
        # A previous process may have died after the HTTP submission. No ID means
        # that we cannot safely distinguish an accepted task from a failed send.
        if job["status"] == "submitting" and not job.get("upstream_task_id"):
            self.write(job, status="uncertain", error="提交结果无法确认。请到 APIMart 核对任务，程序不会重复提交。")
            return
        task = asyncio.create_task(self.run(job))
        self.active[job["id"]] = task
        self.m.CANVAS_TASKS["h3-upgrade:" + job["id"]] = {"status": "running", "type": "minimax-regeneration"}
        task.add_done_callback(lambda _: self.active.pop(job["id"], None))

    async def status(self, job_id):
        job = self.read(job_id)
        self.authorized(job)
        self.launch(job)
        return self.public(job)

    async def lookup(self, provider_id, source_task_id):
        _, identity = self.identity(provider_id, source_task_id)
        with self.db() as db:
            row = db.execute("SELECT payload FROM jobs WHERE source_key=?", (identity,)).fetchone()
        if not row:
            return {"status": "not_found"}
        return await self.status(json.loads(row[0])["id"])

    async def fetch(self, client, provider, task_id):
        base = self.m.video_api_root(provider)
        urls = self.m.video_task_url_candidates(provider, base, task_id, base + "/v1/videos/generations")
        last = None
        for url in urls:
            response = await client.get(url, headers=self.m.api_headers(provider=provider))
            if response.status_code in (404, 405):
                last = response
                continue
            response.raise_for_status()
            return response.json()
        if last is not None:
            last.raise_for_status()
        raise HTTPException(502, "无法查询视频任务。")

    @staticmethod
    def task_data(raw):
        data = raw.get("data", raw)
        return data if isinstance(data, dict) else raw

    async def run(self, job):
        try:
            provider = self.authorized(job)
            async with httpx.AsyncClient(timeout=60) as client:
                if not job.get("upstream_task_id"):
                    self.write(job, status="verifying", error="")
                    raw = await self.fetch(client, provider, job["source_task_id"])
                    data = self.task_data(raw)
                    state = str(data.get("status", data.get("task_status", ""))).upper()
                    if state not in self.m.VIDEO_TASK_SUCCESS_STATUSES:
                        raise HTTPException(400, "原任务尚未成功，不能升级。")
                    if data.get("model") and str(data["model"]).lower() != "minimax-h3":
                        raise HTTPException(400, "原任务不是 MiniMax-H3。")
                    if data.get("resolution") and str(data["resolution"]).upper() != "768P":
                        raise HTTPException(400, "原任务不是 768P 成片。")
                    # Legacy canvas metadata may lack server-side history. The
                    # upstream still enforces account, model and resolution before billing.
                    self.write(job, status="submitting")
                    url = self.m.video_submit_url_candidates(provider, self.m.video_api_root(provider))[0]
                    response = await client.post(url, headers=self.m.api_headers(provider=provider), json={
                        "model": "MiniMax-H3-Regeneration", "source_task_id": job["source_task_id"]})
                    response.raise_for_status()
                    raw = response.json()
                    task_id = self.m.extract_task_id(raw)
                    if not task_id:
                        self.write(job, status="uncertain", error="上游未返回升级任务 ID，请到 APIMart 核对；不会重复提交。")
                        return
                    self.write(job, status="generating", upstream_task_id=str(task_id))
                deadline = time.monotonic() + 900
                while time.monotonic() < deadline:
                    raw = await self.fetch(client, provider, job["upstream_task_id"])
                    data = self.task_data(raw)
                    state = str(data.get("status", data.get("task_status", ""))).upper()
                    if state in self.m.VIDEO_TASK_FAILURE_STATUSES:
                        self.write(job, status="failed", retryable=True, error="2K 再生成失败，768P 原片仍保留；可在 APIMart 查看失败详情。")
                        return
                    urls = self.m.video_output_urls(raw)
                    if urls:
                        break
                    await asyncio.sleep(5)
                else:
                    self.write(job, status="waiting", error="等待上游完成。继续查询会复用已有任务，不会再次计费。")
                    return
                self.write(job, status="downloading", error="")
                outputs = []
                for url in urls:
                    probes = []
                    local = await self.m.save_remote_video_to_output(url, probe_results=probes)
                    aspect = job["source"].get("aspect_ratio", "adaptive")
                    meta = self.m.minimax_h3_video_metadata(probes[0] if probes else {}, aspect)
                    outputs.append(dict(url=local, kind="video", name="MiniMax-H3-2K.mp4", **meta,
                                        video_warning=meta["warning"], h3_request=dict(
                                            provider_id=job["provider_id"], model="MiniMax-H3-Regeneration", resolution="2K",
                                            aspect_ratio=aspect, task_id=job["upstream_task_id"], source_task_id=job["source_task_id"])))
                self.write(job, status="completed", outputs=outputs, error="")
        except asyncio.CancelledError:
            # Preserve the durable state for restart; never start another paid task.
            raise
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", getattr(exc, "status_code", None))
            if job["status"] == "submitting" and not job.get("upstream_task_id"):
                rejected = status in {400, 401, 402, 403, 404, 405, 422, 429}
                self.write(job, status="failed" if rejected else "uncertain", retryable=rejected,
                           error="升级请求被拒绝，原片已保留。请检查账号余额和源任务。" if rejected else
                           "升级提交结果无法确认，请到 APIMart 核对。程序不会自动重复提交。")
            elif job.get("upstream_task_id"):
                self.write(job, status="waiting", error="查询或下载暂时失败；继续查询已有升级任务，不会重新生成。")
            else:
                self.write(job, status="failed", retryable=True,
                           error=str(exc.detail) if isinstance(exc, HTTPException) else "查询原任务失败，请检查网络与 API 配置后重试。")

    async def close(self):
        tasks = list(self.active.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def install_minimax_video(app, module):
    service = MiniMaxVideoService(module)

    @app.post("/api/minimax-regeneration")
    async def start_upgrade(payload: UpgradeRequest):
        return await service.start(payload)

    @app.get("/api/minimax-regeneration")
    async def find_upgrade(provider_id: str, source_task_id: str):
        return await service.lookup(provider_id, source_task_id)

    @app.get("/api/minimax-regeneration/{job_id}")
    async def upgrade_status(job_id: str):
        return await service.status(job_id)

    app.router.add_event_handler("shutdown", service.close)
    return service
