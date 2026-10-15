"""Read-only migration from a compatible Web server into staged desktop data."""
import asyncio
import concurrent.futures
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import urllib.parse
import uuid

import requests
from fastapi import HTTPException
from pydantic import BaseModel


def address(value):
    parsed = urllib.parse.urlsplit(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("请输入不含账号、查询参数的 HTTP 或 HTTPS 旧 Web 地址。")
    if parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"} and parsed.port == 31300:
        raise ValueError("该地址是桌面版本身，请输入旧 Web 地址。")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def resource_path(value, base):
    if not isinstance(value, str):
        return None
    text = value.strip()
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme or parsed.netloc:
        source = urllib.parse.urlsplit(base)
        if (parsed.scheme, parsed.netloc) != (source.scheme, source.netloc):
            return None
        text = parsed.path + (("?" + parsed.query) if parsed.query else "")
    if not text.startswith(("/assets/", "/output/", "/api/storage-files/")):
        return None
    decoded = urllib.parse.unquote(urllib.parse.urlsplit(text).path)
    if "\\" in decoded or any(part in {".", ".."} for part in decoded.split("/")) or any(ord(c) < 32 for c in decoded):
        raise ValueError("旧项目包含不安全的素材路径。")
    return text


def references(value, base, found):
    if isinstance(value, str):
        ref = resource_path(value, base)
        if ref:
            found[value] = ref
    elif isinstance(value, list):
        for item in value:
            references(item, base, found)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key.lower() not in {"api_key", "apikey", "token", "authorization", "cookie"}:
                references(item, base, found)


def rewrite(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [rewrite(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: rewrite(item, mapping) for key, item in value.items()}
    return value


def without_credentials(value):
    if isinstance(value, dict):
        return {key: without_credentials(item) for key, item in value.items() if re.sub(r"[^a-z]", "", key.lower()) not in {"apikey", "xapikey", "token", "accesstoken", "refreshtoken", "authorization", "cookie", "cookies", "password", "secret", "clientsecret", "secretaccesskey", "bearertoken", "credentials", "loginstate"}}
    if isinstance(value, list):
        return [without_credentials(item) for item in value]
    return value


def external_references(value, base, found):
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        parsed, source = urllib.parse.urlsplit(value), urllib.parse.urlsplit(base)
        media = parsed.path.startswith(("/assets/", "/output/", "/api/storage-files/")) or re.search(r"\.(png|jpe?g|webp|gif|mp4|mov|webm|mp3|wav)$", parsed.path, re.I)
        if media and (parsed.scheme, parsed.netloc) != (source.scheme, source.netloc):
            found.add(parsed.path)
    elif isinstance(value, list):
        for item in value:
            external_references(item, base, found)
    elif isinstance(value, dict):
        for item in value.values():
            external_references(item, base, found)


def sha(file):
    with open(file, "rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        return digest.hexdigest()


class Source:
    def __init__(self, base):
        self.base = address(base)
        self.local = threading.local()

    def get(self, route, **kwargs):
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            self.local.session.trust_env = False  # LAN migration never uses a system proxy.
        response = self.local.session.get(self.base + route, timeout=(8, 40), allow_redirects=False, stream=True, **kwargs)
        if response.status_code in {401, 403}:
            response.close()
            raise ValueError("旧端需要登录或访问授权，当前无法读取；请先配置旧端访问权限。")
        if 300 <= response.status_code < 400 and response.status_code != 304:
            response.close()
            raise ValueError("旧端发生跳转，请填写最终访问地址；不会自动访问其他站点。")
        return response

    def json(self, route):
        with self.get(route) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > 32 * 1024 * 1024:
                    raise ValueError("旧端返回的数据过大，无法识别。")
            return json.loads(data.decode("utf-8-sig"))


class Migration:
    def __init__(self, module):
        self.module = module
        self.root = Path(module.DATA_DIR) / "legacy_imports"
        self.root.mkdir(parents=True, exist_ok=True)
        self.sources, self.jobs = {}, {}
        self.lock = threading.Lock()

    @property
    def active(self):
        return sum(job.get("phase") in {"collecting", "downloading", "committing"} for job in list(self.jobs.values()))

    def probe(self, url):
        source = Source(url)
        try:
            info, projects, canvases = source.json("/api/app-info"), source.json("/api/projects"), source.json("/api/canvases")
            if not isinstance(info.get("version"), str) or not isinstance(projects.get("projects"), list) or not isinstance(canvases.get("canvases"), list):
                raise ValueError("接口格式不兼容，未识别为可导入的画布程序。")
            if any(not isinstance(p, dict) or not isinstance(p.get("id"), str) for p in projects["projects"] + canvases["canvases"]):
                raise ValueError("旧端项目或画布列表格式无效。")
        except (requests.RequestException, json.JSONDecodeError, AttributeError):
            raise ValueError("无法连接或识别旧 Web，请检查地址及旧端是否启动。") from None
        key = uuid.uuid4().hex
        self.sources[key] = {"source": source, "projects": projects["projects"], "canvases": canvases["canvases"]}
        return {"source": key, "address": source.base, "version": info["version"], "projects": [{"id": p["id"], "name": str(p.get("name") or "未命名项目"), "canvas_count": sum(str(c.get("project") or "default") == p["id"] for c in canvases["canvases"])} for p in projects["projects"]]}

    def public(self, job):
        return {key: job[key] for key in ("id", "phase", "done", "total", "bytes", "error", "missing", "projects", "result") if key in job}

    def persist(self, job):
        directory = self.root / job["id"]
        directory.mkdir(exist_ok=True)
        temporary = directory / "job.json.tmp"
        temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, directory / "job.json")

    def start(self, key, selected):
        if self.active:
            raise ValueError("已有导入任务正在执行，请等待完成。")
        source = self.sources.get(key)
        if not source or not selected or len(set(selected)) != len(selected) or not set(selected) <= {p["id"] for p in source["projects"]}:
            raise ValueError("请重新检测旧端并选择有效项目。")
        job = {"id": uuid.uuid4().hex, "phase": "collecting", "done": 0, "total": 0, "bytes": 0, "missing": [], "error": "", "address": source["source"].base, "selected": selected, "projects": len(selected), "canvases": []}
        self.jobs[job["id"]] = job
        self.persist(job)
        threading.Thread(target=self.prepare, args=(job,), daemon=True).start()
        return self.public(job)

    def job(self, key):
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            raise ValueError("导入任务无效。")
        if key not in self.jobs:
            file = self.root / key / "job.json"
            if not file.is_file():
                raise ValueError("导入任务不存在。")
            job = json.loads(file.read_text(encoding="utf-8"))
            if job["phase"] == "committing" and job.get("commit_plan"):
                existing = {p["id"] for p in self.module.ensure_default_project()}
                if set(job["commit_plan"]["projects"]) <= existing:
                    job["phase"] = "completed"
                else:
                    for cid in job["commit_plan"]["canvases"]:
                        canvas_file = Path(self.module.CANVAS_DIR) / (cid + ".json")
                        if canvas_file.is_file() and json.loads(canvas_file.read_text(encoding="utf-8")).get("legacy_import", {}).get("job") == key:
                            canvas_file.unlink()
            if job["phase"] not in {"ready", "completed"}:
                job["phase"], job["error"] = "error", "上次导入被中断，可重试并复用已校验的下载。"
            self.jobs[key] = job
        return self.jobs[key]

    def retry(self, key):
        job = self.job(key)
        if self.active or job["phase"] not in {"error", "ready"}:
            raise ValueError("当前任务不能重试。")
        job.update(phase="collecting", done=0, total=0, bytes=0, error="", missing=[])
        threading.Thread(target=self.prepare, args=(job,), daemon=True).start()
        return self.public(job)

    def download(self, source, route, directory):
        token = hashlib.sha256(route.encode()).hexdigest()
        file, metadata = directory / token, directory / (token + ".json")
        previous = json.loads(metadata.read_text()) if metadata.is_file() else {}
        headers = {"If-None-Match": previous["etag"]} if previous.get("etag") and file.is_file() and sha(file) == previous.get("sha256") else {}
        with source.get(route, headers=headers) as response:
            if response.status_code == 304 and headers:
                return {"file": token, "size": file.stat().st_size, "sha256": previous["sha256"]}
            response.raise_for_status()
            if "text/html" in response.headers.get("Content-Type", "").lower():
                raise ValueError("素材地址返回登录页或网页。")
            length = int(response.headers.get("Content-Length") or 0)
            if length and shutil.disk_usage(directory).free < length + 32 * 1024 * 1024:
                raise ValueError("个人数据磁盘空间不足。")
            size, digest = 0, hashlib.sha256()
            with open(str(file) + ".part", "wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > 8 * 1024 ** 3 or shutil.disk_usage(directory).free < len(chunk) + 8 * 1024 ** 2:
                        raise ValueError("素材过大或剩余空间不足。")
                    output.write(chunk)
                    digest.update(chunk)
            if not size or (length and size != length and not response.headers.get("Content-Encoding")):
                raise ValueError("素材下载不完整。")
            os.replace(str(file) + ".part", file)
            record = {"file": token, "size": size, "sha256": digest.hexdigest()}
            metadata.write_text(json.dumps({**record, "etag": response.headers.get("ETag", "")}), encoding="utf-8")
            return record

    def prepare(self, job):
        try:
            source = Source(job["address"])
            projects = source.json("/api/projects")["projects"]
            listing = source.json("/api/canvases")["canvases"]
            chosen = [p for p in projects if p["id"] in job["selected"]]
            if len(chosen) != len(job["selected"]):
                raise ValueError("旧端项目发生变化，请重新检测。")
            canvases, refs, external = [], {}, set()
            for item in listing:
                if str(item.get("project") or "default") not in job["selected"]:
                    continue
                canvas = source.json("/api/canvases/" + urllib.parse.quote(str(item["id"]), safe=""))["canvas"]
                if not isinstance(canvas, dict) or not isinstance(canvas.get("nodes"), list):
                    raise ValueError("画布内容格式不兼容。")
                canvas = without_credentials(canvas)
                canvases.append(canvas)
                references(canvas, source.base, refs)
                external_references(canvas, source.base, external)
            directory = self.root / job["id"] / "downloads"
            directory.mkdir(exist_ok=True)
            routes = sorted(set(refs.values()))
            job.update(phase="downloading", done=len(external), total=len(routes)+len(external), canvases=canvases, project_records=chosen, references=refs, downloaded={}, missing=[{"url": item, "reason": "外部站点素材未自动下载，保留原引用；请在本地补齐。"} for item in sorted(external)])
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = {executor.submit(self.download, source, route, directory): route for route in routes}
                for future in concurrent.futures.as_completed(futures):
                    route = futures[future]
                    try:
                        record = future.result()
                        job["downloaded"][route] = record
                        job["bytes"] += record["size"]
                    except Exception:
                        job["missing"].append({"url": route, "reason": "旧端文件不存在、不可访问或下载未通过检查；可重试。"})
                    job["done"] += 1
            # Detect source edits before local publication; users can retry using cache.
            current = {c["id"]: c.get("updated_at") for c in source.json("/api/canvases")["canvases"]}
            if any(current.get(c["id"]) != c.get("updated_at") for c in canvases):
                raise ValueError("旧端画布在迁移期间发生修改，请暂停编辑后重试。")
            job["phase"] = "ready"
        except ValueError as error:
            job.update(phase="error", error=str(error))
        except Exception:
            job.update(phase="error", error="旧端读取失败，请检查连接后重试。已下载内容仍保留。")
        finally:
            self.persist(job)

    def prepare_commit(self, job):
        # A unique import namespace prevents overwriting any existing media.
        destination = Path(self.module.ASSETS_DIR) / "imports" / job["id"]
        destination.mkdir(parents=True, exist_ok=True)
        mapping = {}
        for original, route in job["references"].items():
            record = job["downloaded"].get(route)
            if not record:
                # Never retain a dependency on the old host for missing local files.
                mapping[original] = "/assets/imports/" + job["id"] + "/missing-" + hashlib.sha256(route.encode()).hexdigest()
                continue
            source = self.root / job["id"] / "downloads" / record["file"]
            if sha(source) != record["sha256"]:
                raise ValueError("已下载的素材校验失败，请重试下载。")
            suffix = Path(urllib.parse.unquote(urllib.parse.urlsplit(route).path)).suffix.lower()
            suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".bin"
            filename = record["file"] + suffix
            target = destination / filename
            if not target.is_file() or sha(target) != record["sha256"]:
                shutil.copy2(source, target)
            mapping[original] = "/assets/imports/" + job["id"] + "/" + filename
        return mapping

    async def commit(self, key, allow_missing=False):
        job = self.job(key)
        if job["phase"] == "completed":
            return self.public(job)
        if self.active or job["phase"] != "ready" or (job["missing"] and not allow_missing):
            raise ValueError("素材未齐全，请重试，或明确选择导入已有内容。")
        job["phase"] = "committing"
        created = []
        try:
            mapping = await asyncio.to_thread(self.prepare_commit, job)
            now = int(time.time() * 1000)
            projects = self.module.ensure_default_project()
            new_projects, project_map = [], {}
            for old in job["project_records"]:
                pid = uuid.uuid4().hex
                project_map[old["id"]] = pid
                new_projects.append({"id": pid, "name": str(old.get("name") or "旧 Web 项目")[:60] + "（旧 Web 导入）", "order": len(projects) + len(new_projects), "created_at": now, "updated_at": now})
            prepared = []
            for old in job["canvases"]:
                canvas = rewrite(copy.deepcopy(old), mapping)
                canvas.update(id=uuid.uuid4().hex, project=project_map[str(old.get("project") or "default")], created_at=now, updated_at=now)
                canvas.pop("deleted_at", None)
                canvas["kind"] = self.module.normalize_canvas_kind(canvas.get("kind"))
                canvas["legacy_import"] = {"job": job["id"], "source_canvas": old["id"]}
                canvas.setdefault("title", "导入画布")
                canvas.setdefault("connections", [])
                canvas.setdefault("viewport", {"x": 0, "y": 0, "scale": 1})
                prepared.append(canvas)
            job["commit_plan"] = {"projects": [p["id"] for p in new_projects], "canvases": [c["id"] for c in prepared]}
            job["result"] = {"projects": len(new_projects), "canvases": len(prepared), "resources": len(job["downloaded"]), "missing": len(job["missing"])}
            self.persist(job)
            for canvas in prepared:
                created.append(Path(self.module.CANVAS_DIR) / (canvas["id"] + ".json"))
                self.module.save_canvas(canvas)
            # No await between reading the registry and final publication.
            registry = Path(getattr(self.module, "PROJECTS_PATH", Path(self.module.DATA_DIR) / "projects.json"))
            temporary = registry.with_name("projects.legacy-" + job["id"] + ".tmp")
            temporary.write_text(json.dumps({"projects": projects + new_projects}, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, registry)
            job["phase"] = "completed"
            try:
                self.persist(job)
            except OSError:
                pass  # Commit journal can rediscover the published project IDs.
            return self.public(job)
        except Exception:
            for file in created:
                file.unlink(missing_ok=True)
            job.update(phase="ready", error="导入未完成，未覆盖已有项目；可重试。")
            self.persist(job)
            raise ValueError(job["error"]) from None


class Detect(BaseModel):
    address: str


class Start(BaseModel):
    source: str
    projects: list[str]


class Commit(BaseModel):
    allow_missing: bool = False


def install(module):
    manager = Migration(module)

    async def invoke(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except ValueError as error:
            raise HTTPException(400, detail=str(error)) from None

    @module.app.post("/desktop/legacy/detect")
    async def detect(payload: Detect):
        return await invoke(manager.probe, payload.address)

    @module.app.post("/desktop/legacy/start")
    async def start(payload: Start):
        return await invoke(manager.start, payload.source, payload.projects)

    @module.app.get("/desktop/legacy/jobs/{key}")
    async def status(key: str):
        return await invoke(lambda: manager.public(manager.job(key)))

    @module.app.post("/desktop/legacy/jobs/{key}/retry")
    async def retry(key: str):
        return await invoke(manager.retry, key)

    @module.app.post("/desktop/legacy/jobs/{key}/commit")
    async def commit(key: str, payload: Commit):
        try:
            return await manager.commit(key, payload.allow_missing)
        except ValueError as error:
            raise HTTPException(400, detail=str(error)) from None

    return manager
