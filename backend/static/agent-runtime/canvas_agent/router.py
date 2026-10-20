import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .service import AgentService
from .store import encode


async def local_origin(request: Request):
    origin = request.headers.get("origin")
    if not origin:
        return
    try:
        parsed = urlsplit(origin)
        host = urlsplit(str(request.base_url))
        allowed = parsed.scheme in ("http", "https") and parsed.hostname in ("127.0.0.1", "localhost", "::1") and parsed.netloc == host.netloc and parsed.scheme == host.scheme and not parsed.username and parsed.path in ("", "/")
    except ValueError:
        allowed = False
    if not allowed:
        raise HTTPException(403, "请求来源不受允许。")


def install_agent(app, host):
    service = AgentService(host)
    router = APIRouter(prefix="/api/canvas-agent", dependencies=[Depends(local_origin)])

    @router.get("/providers")
    async def providers():
        # Registry metadata only. Whitelist fields rather than serializing configured credentials.
        keys = ("id", "name", "chat_models", "image_models", "video_models")
        return {"providers": [{k: p.get(k, [] if k.endswith("models") else "") for k in keys} for p in host["providers"]()]}

    @router.get("/personas")
    async def personas():
        return {"personas": list(service.personas.catalog().values()), "image_concurrency": {"default": 2, "max": 4, "group_max": 2}}

    @router.post("/personas")
    async def save_persona(body: dict = Body(...)):
        try:
            return {"persona": service.personas.save(body)}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.post("/personas/{persona_id}/reset")
    async def reset_persona(persona_id: str, body: dict = Body(...)):
        try:
            return {"persona": service.personas.reset(persona_id, body.get("expected_revision"))}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.get("/capabilities")
    async def capabilities(provider_id: str, model: str, kind: str):
        if kind not in ("image", "video"):
            raise HTTPException(422, "生成类型无效。")
        return {"capabilities": host["capabilities"](provider_id, model, kind)}

    @router.post("/sessions")
    async def create_session(body: dict = Body(...)):
        return {"session": await service.create_session(body.get("canvas_id"), body.get("title"))}

    @router.get("/sessions")
    async def sessions(canvas_id: str):
        await service.canvas(canvas_id)
        return {"sessions": service.store.sessions(canvas_id)}

    @router.get("/sessions/{session_id}/messages")
    async def messages(session_id: str):
        service.require_session(session_id)
        return {"messages": service.store.messages(session_id)}

    @router.post("/sessions/{session_id}/messages", status_code=202)
    async def send(session_id: str, body: dict = Body(...)):
        run = await service.submit(session_id, body)
        return {"run_id": run["id"], "run": run}

    @router.get("/runs/{run_id}")
    async def run(run_id: str):
        await service.start()
        return {"run": service.snapshot(service.require_run(run_id))}

    @router.post("/runs/{run_id}/actions")
    async def action(run_id: str, body: dict = Body(...)):
        return {"run": await service.action(run_id, body)}

    @router.post("/runs/{run_id}/undo")
    async def undo(run_id: str, body: dict = Body(default={})):
        return await service.undo(run_id, body)

    @router.get("/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after_seq: int = Query(default=0, ge=0)):
        await service.start()
        service.require_run(run_id)
        try:
            after_seq = max(after_seq, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(422, "事件序号无效。") from None

        async def stream():
            after = after_seq
            heartbeat = 0
            while not await request.is_disconnected():
                records = service.store.events(run_id, after)
                for record in records:
                    after = record["seq"]
                    yield f'id: {after}\nevent: {record["type"]}\ndata: {encode(record["payload"])}\n\n'
                heartbeat += 1
                if heartbeat >= 20:
                    yield ": heartbeat\n\n"
                    heartbeat = 0
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.include_router(router)
    app.router.on_startup.append(service.start)
    app.router.on_shutdown.append(service.close)
    return service
