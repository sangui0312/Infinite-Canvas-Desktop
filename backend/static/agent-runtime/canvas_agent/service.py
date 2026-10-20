import asyncio
import copy
import hashlib
import inspect
import json
import time

from fastapi import HTTPException

from .planning import PlanError, SYSTEM, TOOLS, configured, context_snapshot, models, parse_response, parse_text, validate_plan, validate_settings, legacy_questions
from .store import Store, encode, uid
from .personas import PERSONAS, PersonaLibrary


async def invoke(function, *args, **kwargs):
    value = function(*args, **kwargs)
    return await value if inspect.isawaitable(value) else value


class AgentService:
    def __init__(self, host):
        self.host = host
        self.store = Store(host["data_dir"])
        self.personas = PersonaLibrary(self.store)
        self.runs = {r["id"]: r for r in self.store.all()}
        self.tasks = {}
        self.lock = asyncio.Lock()
        self.started = False
        self.closing = False
        self.delivery_locks = {}

    def _spawn(self, key, coroutine):
        if key in self.tasks and not self.tasks[key].done():
            coroutine.close()
            return
        task = asyncio.create_task(coroutine)
        self.tasks[key] = task
        # Existing desktop shells already count this registry before restarting.
        # Track background Agent work there without changing immutable shell files.
        registry = self.host.get("active_task_registry")
        record = {"status": "running"}
        if registry is not None:
            registry["agent:" + key] = record
            def finished(_task):
                if registry.get("agent:" + key) is record:
                    registry.pop("agent:" + key, None)
            task.add_done_callback(finished)
        # Consume unexpected exceptions without printing sensitive upstream details.
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())

    async def start(self):
        if self.started:
            return
        self.started = True
        for run in self.runs.values():
            if run["status"] == "planning":
                run["status"], run["error"] = "interrupted", "规划被中断，请重新发送需求。"
                self.store.save(run, "needs_attention")
            elif run["status"] in ("running", "stopping"):
                run["status"] = "interrupted"
                for step in run["steps"]:
                    if step["status"] in ("submitting", "waiting"):
                        step["status"] = "waiting" if step.get("upstream_id") else "unknown"
                        if not step.get("upstream_id"):
                            step["error"] = "提交是否受理未知，禁止自动重发。"
                self.store.save(run, "needs_attention")
            if any(s["status"] in ("waiting", "delivery_failed", "delivering") or (s["status"] == "generated") for s in run.get("steps", [])):
                self._spawn("recover:" + run["id"], self._recover(run))

    async def close(self):
        self.closing = True
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        # Store owns no long-lived connections; no database handle blocks data migration.

    def require_session(self, session_id):
        session = self.store.session(session_id)
        if not session:
            raise HTTPException(404, "会话不存在。")
        return session

    def require_run(self, run_id):
        run = self.runs.get(run_id)
        if not run:
            raise HTTPException(404, "任务不存在。")
        return run

    async def canvas(self, canvas_id):
        if not isinstance(canvas_id, str) or not canvas_id or len(canvas_id) > 200 or any(c in canvas_id for c in ("/", "\\", "\x00")) or canvas_id in (".", ".."):
            raise HTTPException(422, "画布 ID 无效。")
        canvas = await invoke(self.host["load_canvas"], canvas_id)
        if not canvas or not isinstance(canvas, dict):
            raise HTTPException(404, "画布不存在。")
        return canvas

    async def create_session(self, canvas_id, title="创作会话"):
        await self.canvas(canvas_id)
        return self.store.create_session(canvas_id, str(title or "创作会话"))

    def snapshot(self, run):
        result = {k: copy.deepcopy(v) for k, v in run.items() if k not in ("request", "original_request", "context", "client_request_id", "revision_context", "persona_snapshot")}
        role = self._role_snapshot(run)
        result.update(persona_name=role["name"], persona_revision=role["revision"],
                      persona_changed=self.personas.get(role["id"])["revision"] != role["revision"])
        result["outputs"] = [{**copy.deepcopy(output), "node_ids": step.get("node_ids", []), "operation_id": step.get("operation_id")} for step in run["steps"] for output in step.get("outputs", [])]
        result["messages"] = [m for m in self.store.messages(run["session_id"]) if m.get("run_id") == run["id"]]
        status = run["status"]
        if status == "needs_input" and result.get("plan") and not result["plan"].get("questions"):
            result["plan"]["questions"] = legacy_questions(result["plan"].get("summary", ""), result["plan"].get("steps"))
        actions = []
        if status == "awaiting_approval":
            actions.extend(("approve", "revise"))
        if status == "needs_input":
            actions.append("revise")
        if status == "partial" and any(s["status"] == "blocked" for s in run["steps"]):
            actions.append("revise")
        if status in ("planning", "awaiting_approval", "running", "stopping"):
            actions.append("stop")
        if status in ("stopped", "interrupted", "partial", "needs_attention") and run.get("approved_revision") and any(s["status"] == "pending" for s in run["steps"]):
            actions.append("continue")
        if status not in ("planning", "running", "stopping") and any(s["status"] == "failed" for s in run["steps"]):
            actions.append("retry")
        if status not in ("running", "stopping") and any(s["status"] in ("generated", "delivery_failed", "delivering") for s in run["steps"]):
            actions.append("repair_delivery")
        if any(s.get("delivery_status") == "delivered" for s in run["steps"]):
            actions.append("undo")
        result["actions"] = actions
        return result

    def _role_snapshot(self, run):
        if run.get("persona_snapshot"):
            return copy.deepcopy(run["persona_snapshot"])
        # Pre-library tasks retain the original built-in role, not later user edits.
        key = run.get("persona_id", "general")
        return {**copy.deepcopy(PERSONAS.get(key, PERSONAS["general"])), "id": key, "revision": 0}

    async def submit(self, session_id, body):
        await self.start()
        body = copy.deepcopy(body)
        try:
            selected_options = self.personas.options(body)
            selected_role = self.personas.get(selected_options["persona_id"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if self.host.get("normalize_generation"):
            body["generation"] = self.host["normalize_generation"](body.get("generation") or {}, (body.get("generation") or {}).get("apiKind"))
        defaults = body.get("generation_defaults", {})
        if not isinstance(defaults, dict) or set(defaults) - {"image", "video"} or any(not isinstance(v, dict) for v in defaults.values()):
            raise HTTPException(422, "图片/视频默认参数无效。")
        if self.host.get("normalize_generation"):
            body["generation_defaults"] = {kind: self.host["normalize_generation"](value, kind) for kind, value in defaults.items()}
        session = self.require_session(session_id)
        request_id = body.get("client_request_id")
        text = body.get("text")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 160 or not isinstance(text, str) or not text.strip() or len(text) > 24000:
            raise HTTPException(422, "消息内容或幂等请求 ID 无效。")
        async with self.lock:
            prior = self.store.lookup(session_id, request_id)
            if prior:
                if prior.get("original_request", prior["request"]) != body:
                    raise HTTPException(409, "同一请求 ID 不能用于不同消息。")
                return self.snapshot(self.runs[prior["id"]])
            canvas = await self.canvas(session["canvas_id"])
            try:
                llm = body.get("llm") or {}
                configured(self.host, llm.get("provider_id"), llm.get("model"), "chat")
                snapshot = context_snapshot(canvas, body.get("context") or {})
                # A reference such as "the second result" binds to actual outcomes in this
                # session, never a similarly named node elsewhere on the canvas.
                canvas_ids = {n["id"] for n in canvas.get("nodes", [])}
                prior_ids = list(dict.fromkeys(node_id for r in self.runs.values() if r["session_id"] == session_id for s in r.get("steps", []) for node_id in s.get("node_ids", []) if node_id in canvas_ids))
                if prior_ids:
                    extended = context_snapshot(canvas, {"selected_node_ids": list(dict.fromkeys(snapshot["allowed_node_ids"] + prior_ids))})
                    extended["selected_node_ids"] = snapshot["selected_node_ids"]
                    snapshot = extended
            except PlanError as exc:
                raise HTTPException(422, str(exc)) from None
            run = {"id": uid(), "session_id": session_id, "canvas_id": session["canvas_id"], "client_request_id": request_id, "status": "planning", "revision": 0, "plan": None, "steps": [], "stop_requested": False, "created_at": time.time(), "request": copy.deepcopy(body), "context": snapshot}
            run.update(selected_options)
            run["persona_snapshot"] = selected_role
            self.store.insert(run, text, snapshot["selected_node_ids"])
            self.runs[run["id"]] = run
            self.store.save(run, "planning")
            self._spawn("plan:" + run["id"], self._plan(run))
            return self.snapshot(run)

    async def _plan(self, run):
        try:
            history = self.store.messages(run["session_id"])[-16:]
            providers = self.host["providers"]()
            catalog = []
            for p in providers:
                for kind in ("image", "video"):
                    for model in models(p, kind):
                        try:
                            cap = self.host["capabilities"](p["id"], model, kind)
                            catalog.append({"provider_id": p["id"], "model": model, "kind": kind, "capabilities": cap})
                        except Exception:
                            pass  # Undeclared adapters remain unavailable, never guessed.
            messages = [{"role": "system", "content": SYSTEM}, *[{"role": m["role"], "content": m["content"]} for m in history if m["run_id"] != run["id"]], {"role": "user", "content": encode({"request": run["request"]["text"], "generation": run["request"].get("generation", {}), "context": {k: v for k, v in run["context"].items() if k != "media"}, "providers": providers, "prior_outputs": [{"step_id": s["id"], "outputs": [{"node_ids": s.get("node_ids", []), "kind": o["kind"]} for o in s.get("outputs", [])]} for r in self.runs.values() if r["session_id"] == run["session_id"] for s in r.get("steps", []) if s.get("outputs")]})}]
            if run.get("revision_context"):
                messages.append({"role": "user", "content": "请保留当前计划中未要求修改的内容，按最新要求修订完整计划：" + encode({"current_plan": run["revision_context"], "latest_request": run["request"]["text"]})})
            role = self._role_snapshot(run)
            messages[0]["content"] += "\n\n创作人设：" + role["name"] + "\n" + role["prompt"]
            messages[0]["content"] += "\n\n以下能力目录由服务端从当前配置读取，已完成能力查询；即使对话模型不支持工具调用也可直接使用。没有匹配的能力时返回 status=needs_input 并说明缺少什么，不要虚构参数。\n" + encode({"capability_catalog": catalog, "generation_defaults": run["request"].get("generation_defaults", {}), "legacy_default": run["request"].get("generation", {})})
            llm = run["request"]["llm"]
            tools, correction, decisions = TOOLS, 0, 0
            for _ in range(12):
                if run["stop_requested"]:
                    return
                try:
                    response = await invoke(self.host["llm"], messages, llm["provider_id"], llm["model"], tools=tools)
                except HTTPException as exc:
                    detail = str(exc.detail).lower()
                    if tools is not None and exc.status_code in (400, 422) and ("tool" in detail and any(x in detail for x in ("unsupported", "not support", "unknown parameter", "不支持"))):
                        tools = None
                        continue
                    raise
                if run["stop_requested"]:
                    return
                message = parse_response(response)
                calls = message.get("tool_calls") or []
                try:
                    if calls:
                        decisions += len(calls)
                        if len(calls) > 4 or decisions > 12:
                            raise PlanError("单次工具调用过多。")
                        messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
                        plan = None
                        for call in calls:
                            fn = call.get("function", {})
                            try:
                                arguments = json.loads(fn.get("arguments", "{}"))
                            except (ValueError, TypeError):
                                raise PlanError("工具参数 JSON 无效。") from None
                            if fn.get("name") == "submit_plan":
                                if plan is not None:
                                    raise PlanError("一次只能提交一个计划。")
                                plan = validate_plan(arguments, run["request"], run["context"], self.host)
                                result = {"status": "awaiting_user_approval", "plan_revision": run.get("next_plan_revision", 1)}
                            elif fn.get("name") == "canvas_get_context" and arguments == {}:
                                result = {k: v for k, v in run["context"].items() if k != "media"}
                            elif fn.get("name") == "model_get_capabilities" and isinstance(arguments, dict) and set(arguments) == {"provider_id", "model", "kind"} and arguments["kind"] in ("image", "video"):
                                configured(self.host, arguments["provider_id"], arguments["model"], arguments["kind"])
                                result = self.host["capabilities"](arguments["provider_id"], arguments["model"], arguments["kind"])
                            else:
                                raise PlanError("模型请求了未允许工具或参数。")
                            run.setdefault("tool_calls", []).append({"id": call.get("id", ""), "name": fn["name"], "args_hash": hashlib.sha256(encode(arguments).encode()).hexdigest(), "status": "validated"})
                            self.store.save(run, "tool_completed", {"tool_call_id": call.get("id", ""), "tool": fn["name"]})
                            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": encode(result)})
                        if plan is None:
                            continue
                    else:
                        plan = validate_plan(parse_text(message.get("content")), run["request"], run["context"], self.host)
                    plan["revision"] = run.get("next_plan_revision", 1)
                    plan.update(self.personas.options(run))
                    plan.update(persona_name=role["name"], persona_revision=role["revision"])
                    run["plan"] = plan
                    run["steps"] = self._planned_steps(plan)
                    run["status"] = self._plan_status(plan)
                    explanation = plan["summary"] or ("请确认以上制作安排与参数后开始。" if plan["steps"] else "请补充具体的创作需求。")
                    if plan.get("questions"):
                        explanation += "\n\n待你回答：\n" + "\n".join(f'{i+1}. {q["question"]}' + ("（可选：" + "；".join(q["options"]) + "）" if q["options"] else "") for i, q in enumerate(plan["questions"]))
                    self.store.message(run["session_id"], run["id"], "assistant", explanation)
                    self.store.save(run, "run_completed" if run["status"] == "completed" else "plan_ready", {"status": run["status"], "plan": plan})
                    return
                except (PlanError, TypeError, KeyError) as exc:
                    if correction >= 1:
                        raise PlanError(str(exc) if isinstance(exc, PlanError) else "计划结构无效。") from None
                    correction += 1
                    if calls:
                        answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
                        for call in calls:
                            if call.get("id") not in answered:
                                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": encode({"error": str(exc) if isinstance(exc, PlanError) else "计划格式无效"})})
                    messages.append({"role": "user", "content": "计划未通过校验，请只修正一次并重新返回完整计划：" + (str(exc) if isinstance(exc, PlanError) else "格式无效")})
            raise PlanError("已达到规划决策次数上限，请缩小需求。")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if run["stop_requested"]:
                return
            run["status"] = "failed"
            run["error"] = str(exc) if isinstance(exc, PlanError) else "规划服务请求未完成，请检查平台配置或稍后重试。"
            self.store.message(run["session_id"], run["id"], "assistant", run["error"])
            self.store.save(run, "run_failed")

    @staticmethod
    def _planned_steps(plan, previous=None):
        existing = {s["id"]: s for s in previous or []}
        return [copy.deepcopy(existing[s["id"]]) if existing.get(s["id"], {}).get("status") not in (None, "pending", "blocked") else {**copy.deepcopy(s), "status": "blocked" if s.get("blocked_reason") else "pending", "attempt": 0, "outputs": [], "delivery_status": "pending"} for s in plan["steps"]]

    @staticmethod
    def _plan_status(plan, steps=None):
        if plan.get("status") == "needs_input" and plan.get("questions"):
            return "needs_input"
        if (any(s["status"] == "pending" for s in steps) if steps is not None else any(not s.get("blocked_reason") for s in plan["steps"])):
            return "awaiting_approval"
        if steps and any(s["status"] == "completed" for s in steps):
            return "partial" if any(s["status"] == "blocked" for s in steps) else "completed"
        return "needs_input" if plan["steps"] or plan.get("status") == "needs_input" else "completed"

    def _canvas_busy(self, run):
        return any(r["id"] != run["id"] and r["canvas_id"] == run["canvas_id"] and (r["status"] in ("running", "stopping") or any(s["status"] in ("submitting", "waiting", "unknown") for s in r.get("steps", []))) for r in self.runs.values())

    async def action(self, run_id, body):
        await self.start()
        async with self.lock:
            run = self.require_run(run_id)
            request_id, action = body.get("request_id"), body.get("action")
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 160:
                raise HTTPException(422, "动作请求 ID 无效。")
            fingerprint = hashlib.sha256(encode(body).encode()).hexdigest()
            prior = self.store.action(run_id, request_id)
            if prior:
                if prior[0] != fingerprint:
                    raise HTTPException(409, "动作请求 ID 已用于其他参数。")
                return self.snapshot(run)
            if body.get("expected_revision") != run["revision"] and action != "stop":
                raise HTTPException(409, "任务状态已变化，请刷新后重试。")
            if action not in self.snapshot(run)["actions"] or action == "undo":
                raise HTTPException(409, "当前状态不允许此动作。")
            if action == "revise":
                if body.get("plan_revision") != run["plan"]["revision"]:
                    raise HTTPException(409, "计划版本已变化，请刷新后修改。")
                text = body.get("text", "")
                edits = body.get("steps", [])
                try:
                    revised_options = self.personas.options(body, run)
                    revised_role = self.personas.get(revised_options["persona_id"])
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from None
                role_changed = revised_role["id"] != self._role_snapshot(run)["id"] or revised_role["revision"] != self._role_snapshot(run)["revision"]
                config_changed = revised_options != self.personas.options(run) or role_changed
                if not isinstance(text, str) or len(text) > 16000 or not isinstance(edits, list) or (not text.strip() and not edits and not config_changed):
                    raise HTTPException(422, "请填写修改要求或生成参数。")
                if len(run["request"]["text"]) + len(text) > 23000:
                    raise HTTPException(422, "本轮修改内容过长，请新建会话。")
                plan = copy.deepcopy(run["plan"])
                try:
                    seen = set()
                    for edit in edits:
                        if not isinstance(edit, dict) or not isinstance(edit.get("id"), str) or set(edit) - {"id", "provider_id", "model", "settings"} or edit.get("id") in seen:
                            raise PlanError("计划参数修改无效。")
                        seen.add(edit.get("id"))
                        step = next((s for s in plan["steps"] if s["id"] == edit.get("id") and s["kind"] in ("image", "video")), None)
                        if step is None or not isinstance(edit.get("settings"), dict):
                            raise PlanError("找不到可修改的生成步骤。")
                        if any(s["id"] == step["id"] and s["status"] not in ("pending", "blocked") for s in run["steps"]):
                            raise PlanError("此步骤已经提交，不能修改或重新生成。")
                        step.update({k: edit[k] for k in ("provider_id", "model", "settings") if k in edit})
                        configured(self.host, step["provider_id"], step["model"], step["kind"])
                        allowed = {"ratio", "resolution", "duration", "size_mode", "quality", "seed", "fps", "version", "background", "output_format", "output_compression", "watermark", "return_last_frame", "generate_audio", "multimodal", "use_frame_roles", "camerafixed", "trusted_asset", "enhance_prompt", "enable_upsample", "transparent_background"}
                        if set(step["settings"]) - allowed:
                            raise PlanError("包含未允许的生成参数。")
                        step["settings"] = validate_settings(self.host, step, [r for r in step["references"] if "url" in r])
                        step.pop("blocked_reason", None)
                        step.pop("parameter_problem", None)
                except (PlanError, HTTPException) as exc:
                    raise HTTPException(422, str(exc) if isinstance(exc, PlanError) else str(exc.detail)) from None
                plan["revision"] += 1
                plan.update(revised_options)
                replan = bool(text.strip()) or role_changed
                if replan and any(s.get("attempt", 0) for s in run["steps"]):
                    raise HTTPException(422, "已有生成成果，请只调整未提交步骤的参数；新创作请发送新的需求。")
                run.update(revised_options)
                run["persona_snapshot"] = revised_role
                run.pop("approved_revision", None)
                run.pop("error", None)
                if replan:
                    # Replan only unsubmitted work, retaining its authorized references.
                    run.setdefault("original_request", copy.deepcopy(run["request"]))
                    run["revision_context"] = [{k: copy.deepcopy(s[k]) for k in ("id", "title", "kind", "prompt", "provider_id", "model", "settings", "dependencies", "loop", "operation") if k in s} for s in plan["steps"]]
                    text = text.strip() or "使用" + revised_role["name"] + "重新完善当前计划。"
                    run["request"]["text"] += "\n\n用户修订要求：" + text
                    if body.get("text", "").strip():
                        for key in ("ratio", "resolution", "duration", "size_mode", "sizeMode"):
                            run["request"].get("generation", {}).pop(key, None)
                    run["next_plan_revision"] = plan["revision"]
                    run["status"] = "planning"
                    run["stop_requested"] = False
                    self.store.message(run["session_id"], run["id"], "user", text.strip())
                else:
                    run["plan"] = plan
                    by_id = {s["id"]: s for s in plan["steps"]}
                    for step in plan["steps"]:
                        dependency_blocked = any(by_id[d].get("blocked_reason") for d in step["dependencies"])
                        if dependency_blocked:
                            step["blocked_reason"] = "前置步骤尚未就绪，请先调整其参数。"
                        elif step.get("parameter_problem"):
                            step["blocked_reason"] = step["parameter_problem"]
                        elif step.get("blocked_reason") == "前置步骤尚未就绪，请先调整其参数。":
                            step.pop("blocked_reason", None)
                    run["steps"] = self._planned_steps(plan, run["steps"])
                    run["status"] = self._plan_status(plan, run["steps"])
                self.store.save(run, "plan_revised")
                if replan:
                    self._spawn("plan:" + run_id, self._plan(run))
            elif action == "stop":
                run["stop_requested"] = True
                run["status"] = "stopping" if any(s["status"] in ("submitting", "waiting") for s in run["steps"]) else "stopped"
                self.store.save(run, "stop_requested")
            else:
                if self._canvas_busy(run):
                    raise HTTPException(409, "此画布已有执行中或状态未明的任务，请先处理。")
                if any(s["status"] in ("unknown", "submitting", "waiting") for s in run["steps"]):
                    raise HTTPException(409, "已有任务状态未明确，禁止重新提交。")
                if action == "approve":
                    if self.snapshot(run)["persona_changed"]:
                        raise HTTPException(409, "人设提示词已修改，请应用人设重新规划，再批准新计划。")
                    if body.get("plan_revision") != run["plan"]["revision"]:
                        raise HTTPException(409, "计划版本已变化，请重新确认。")
                    run["approved_revision"] = run["plan"]["revision"]
                if action == "retry":
                    failed = [s for s in run["steps"] if s["status"] == "failed" and (not body.get("step_id") or s["id"] == body["step_id"])]
                    if not failed:
                        raise HTTPException(409, "没有可重试的明确失败步骤。")
                    for step in failed:
                        step["status"] = "pending"
                        step.pop("error", None)
                        step.pop("upstream_id", None)
                run["stop_requested"] = False
                run.pop("error", None)
                run["status"] = "running"
                self.store.save(run, "run_started")
                self._spawn("execute:" + run_id, self._execute(run, delivery_only=action == "repair_delivery"))
            result = self.snapshot(run)
            self.store.action(run_id, request_id, fingerprint, result)
            return result

    async def _deliver(self, run, step):
        # Generation overlaps, while canvas mutations remain ordered per canvas.
        async with self.delivery_locks.setdefault(run["canvas_id"], asyncio.Lock()):
            await self._deliver_unlocked(run, step)

    async def _deliver_unlocked(self, run, step):
        if step.get("delivery_status") == "delivered":
            return
        operation_id = step.setdefault("operation_id", "agent-" + run["id"] + "-" + step["id"])
        if step["kind"] in ("image", "video"):
            operation = {"type": "create_outputs", "outputs": copy.deepcopy(step["outputs"]), "source_node_ids": list(dict.fromkeys(r["node_id"] for r in step.get("resolved_references", step["references"]) if r.get("node_id"))), "title": step["title"]}
        else:
            operation = copy.deepcopy(step["operation"])
        operation.update(run_id=run["id"], step_id=step["id"])
        step["delivery_status"], step["status"] = "prepared", "delivering"
        step["prepared_operation"] = operation
        self.store.save(run, "delivery_prepared", {"step_id": step["id"], "operation_id": operation_id})
        try:
            result = await invoke(self.host["apply_operation"], run["canvas_id"], operation_id, operation)
            step["node_ids"] = result.get("node_ids", [])
            step["delivery_status"], step["status"] = "delivered", "completed"
            step.pop("error", None)
            self.store.save(run, "canvas_updated", {"step_id": step["id"], "operation_id": operation_id, "node_ids": step["node_ids"]})
            try:
                await invoke(self.host["broadcast"], run["canvas_id"])
            except Exception:
                pass  # Delivery is committed; a notification failure must never replay generation.
        except Exception:
            step["delivery_status"], step["status"] = "failed", "delivery_failed"
            step["error"] = "成果已保留，画布交付失败或发生冲突；可重试交付。"
            self.store.save(run, "needs_attention", {"step_id": step["id"], "error": step["error"]})

    def _record_result(self, run, step, result):
        if not isinstance(result, dict):
            step["status"], step["error"] = "unknown", "上游结果格式无法确认，请勿重复提交。"
        elif result.get("outputs"):
            outputs = []
            for index, item in enumerate(result["outputs"]):
                if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not item["url"]:
                    continue
                outputs.append({"id": step["task_id"] + f"-{index}", "url": item["url"], "kind": item.get("kind", step["kind"]), **{k: item[k] for k in ("width", "height", "name") if k in item}})
            if not outputs:
                step["status"], step["error"] = "unknown", "未取得可用成果，禁止自动重发。"
            else:
                step["outputs"], step["status"] = outputs, "generated"
        elif result.get("status") == "failed":
            step["status"], step["error"] = "failed", "上游已明确报告生成失败，可仅重试此步骤。"
        elif result.get("status") in ("running", "pending", "submitted") and step.get("upstream_id"):
            step["status"] = "waiting"
        else:
            step["status"], step["error"] = "unknown", "上游处理结果尚未明确，禁止自动重发。"
        self.store.save(run, "generation_updated", {"step_id": step["id"], "status": step["status"]})

    async def _query(self, run, step):
        try:
            result = await invoke(self.host["query_task"], copy.deepcopy(step))
            self._record_result(run, step, result)
            if step["status"] == "generated":
                await self._deliver(run, step)
        except asyncio.CancelledError:
            raise
        except Exception:
            step["status"], step["error"] = "waiting", "查询暂未完成，保留同一上游任务继续查询。"
            self.store.save(run, "needs_attention", {"step_id": step["id"], "status": "waiting"})

    async def _recover(self, run):
        while not self.closing:
            waiting = False
            for step in run["steps"]:
                if step["status"] == "waiting" and step.get("upstream_id"):
                    await self._query(run, step)
                    waiting = waiting or step["status"] == "waiting"
                elif step["status"] in ("generated", "delivery_failed", "delivering"):
                    await self._deliver(run, step)
            self._finish(run, recovered=True)
            if not waiting:
                return
            await asyncio.sleep(float(self.host.get("poll_interval", 3)))

    def _finish(self, run, recovered=False):
        states = [s["status"] for s in run["steps"]]
        if all(s == "completed" for s in states):
            run["status"] = "completed"
        elif any(s in ("unknown", "waiting", "submitting", "delivery_failed", "delivering") for s in states):
            run["status"] = "needs_attention"
        elif run["stop_requested"]:
            run["status"] = "stopped"
        elif recovered and "pending" in states:
            run["status"] = "interrupted"
        elif "failed" in states:
            run["status"] = "partial" if "completed" in states else "failed"
        elif "blocked" in states:
            run["status"] = "partial" if "completed" in states else "needs_input"
        elif "pending" in states:
            run["status"] = "interrupted"
        else:
            run["status"] = "completed"
        self.store.save(run, "run_completed" if run["status"] == "completed" else "needs_attention")

    async def _execute_step(self, run, step):
        if step["status"] in ("generated", "delivery_failed", "delivering"):
            await self._deliver(run, step)
            return
        if run["stop_requested"] or step["status"] != "pending":
            return
        if step["kind"] not in ("image", "video"):
            await self._deliver(run, step)
            return
        references = []
        for ref in step["references"]:
            if "step_id" in ref:
                dependency = next(s for s in run["steps"] if s["id"] == ref["step_id"])
                for i, output in enumerate(dependency.get("outputs", [])):
                    references.append({**output, "node_id": (dependency.get("node_ids") or [""])[min(i, max(0, len(dependency.get("node_ids", [])) - 1))]})
            else:
                references.append(copy.deepcopy(ref))
        # Recheck current references and configured capabilities immediately before a paid submission.
        try:
            canvas = await self.canvas(run["canvas_id"])
            current = context_snapshot(canvas, {"selected_node_ids": run["context"]["selected_node_ids"], "scope": "canvas"})
            urls = {(r["node_id"], r["url"]) for refs in current["media"].values() for r in refs}
            if any((r.get("node_id"), r["url"]) not in urls for r in references):
                raise PlanError("引用素材已变化，请重新规划。")
            configured(self.host, step["provider_id"], step["model"], step["kind"])
            validated = validate_settings(self.host, step, references)
            if validated != step["settings"]:
                raise PlanError("模型参数契约已变化，请重新规划。")
        except Exception:
            step["status"], step["error"] = "failed", "引用素材或模型参数已变化，未提交生成，请重新规划。"
            self.store.save(run, "needs_attention")
            return
        if run["stop_requested"]:
            return
        step["attempt"] += 1
        step["task_id"] = f'{run["id"]}:{step["id"]}:{step["attempt"]}'
        step["status"] = "submitting"
        step["resolved_references"] = references
        spec = {"prompt": step["prompt"], "provider_id": step["provider_id"], "model": step["model"], "kind": step["kind"], "references": references, "settings": copy.deepcopy(step["settings"]), "request_id": step["task_id"]}
        step["submitted_spec"] = spec
        self.store.save(run, "generation_submitting", {"step_id": step["id"], "task_id": step["task_id"]})

        async def submitted(upstream_id, metadata=None):
            if not upstream_id:
                raise ValueError("Missing upstream task identity")
            step["upstream_id"] = str(upstream_id)
            # Persist only adapter-owned recovery fields, never arbitrary headers or response bodies.
            step["recovery"] = {k: v for k, v in (metadata or {}).items() if k in ("query_kind", "submitted_at", "api_kind") and isinstance(v, (str, int, float, bool))}
            step["status"] = "waiting"
            self.store.save(run, "generation_submitted", {"step_id": step["id"], "status": "waiting"})

        try:
            result = await invoke(self.host["generate"], spec, submitted)
            if isinstance(result, dict) and result.get("upstream_id") and not step.get("upstream_id"):
                await submitted(result["upstream_id"])
            self._record_result(run, step, result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, HTTPException) and 400 <= exc.status_code < 500 and exc.status_code not in (408, 409, 429) and not step.get("upstream_id"):
                step["status"], step["error"] = "failed", "上游明确拒绝生成请求，请检查参数后重试。"
            else:
                step["status"] = "waiting" if step.get("upstream_id") else "unknown"
                step["error"] = "连接中断，已保留任务身份；禁止重复提交。"
            self.store.save(run, "needs_attention", {"step_id": step["id"], "status": step["status"]})
        if step["status"] == "generated":
            await self._deliver(run, step)
        # A known remote job occupies its slot until it settles. Never POST it again.
        while step["status"] == "waiting" and step.get("upstream_id") and not self.closing:
            await self._query(run, step)
            if step["status"] == "waiting":
                await asyncio.sleep(float(self.host.get("poll_interval", 3)))

    async def _execute(self, run, delivery_only=False):
        workers = {}
        try:
            if delivery_only:
                for step in run["steps"]:
                    if step["status"] in ("generated", "delivery_failed", "delivering"):
                        await self._deliver(run, step)
                self._finish(run)
                return
            limit = (run.get("plan") or {}).get("image_concurrency", run.get("image_concurrency", 2))
            limit = max(1, min(4, int(limit)))
            while not self.closing:
                # No new paid submissions after a stop or an ambiguous submission.
                paused = run["stop_requested"] or any(s["status"] == "unknown" for s in run["steps"])
                if not paused:
                    for step in run["steps"]:
                        if step["id"] in {s["id"] for s in workers.values()} or step["status"] != "pending":
                            continue
                        if any(next(s for s in run["steps"] if s["id"] == dep)["status"] != "completed" for dep in step["dependencies"]):
                            continue
                        active = list(workers.values())
                        if step["kind"] == "image":
                            if sum(s["kind"] == "image" for s in active) >= limit:
                                continue
                            if step.get("loop") == "group" and sum(s["kind"] == "image" and s.get("loop") == "group" for s in active) >= 2:
                                continue
                        elif step["kind"] == "video":
                            if any(s["kind"] == "video" for s in active):
                                continue
                        elif active:
                            continue
                        task = asyncio.create_task(self._execute_step(run, step))
                        workers[task] = step
                if not workers:
                    break
                completed, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
                for task in completed:
                    step = workers.pop(task)
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        step["status"] = "waiting" if step.get("upstream_id") else "unknown"
                        step["error"] = "任务中断，已保留现有状态；禁止重复提交。"
                        self.store.save(run, "needs_attention")
            self._finish(run)
        except asyncio.CancelledError:
            raise
        except Exception:
            run["status"], run["error"] = "needs_attention", "任务被中断，已保存现有进度，请刷新查看。"
            self.store.save(run, "needs_attention")
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()
            if workers:
                await asyncio.gather(*workers, return_exceptions=True)

    async def undo(self, run_id, body):
        async with self.lock:
            run = self.require_run(run_id)
            request_id = body.get("request_id") or "undo:" + str(body.get("operation_id") or "all")
            fingerprint = hashlib.sha256(encode({"undo": body.get("operation_id")} ).encode()).hexdigest()
            prior = self.store.action(run_id, request_id)
            if prior:
                if prior[0] != fingerprint:
                    raise HTTPException(409, "请求 ID 已使用。")
                return prior[1]
            if run["status"] in ("running", "stopping", "planning"):
                raise HTTPException(409, "请先停止任务再撤销。")
            steps = [s for s in reversed(run["steps"]) if s.get("delivery_status") == "delivered" and (not body.get("operation_id") or s.get("operation_id") == body["operation_id"])]
            if not steps:
                raise HTTPException(409, "没有可撤销的本轮操作。")
            results = []
            for step in steps:
                result = await invoke(self.host["undo_operation"], run["canvas_id"], step["operation_id"])
                results.append(result)
                if not result.get("conflicts"):
                    step["delivery_status"] = "undone"
                    step["undone"] = True
                self.store.save(run, "operation_undone", {"operation_id": step["operation_id"], "conflicts": result.get("conflicts", [])})
            response = {"results": results, "run": self.snapshot(run)}
            self.store.action(run_id, request_id, fingerprint, response)
            try:
                await invoke(self.host["broadcast"], run["canvas_id"])
            except Exception:
                pass
            return response
