import contextvars
from pathlib import Path
import sys
import tempfile
import types
import unittest

from fastapi import HTTPException
import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "static" / "agent-runtime"))
from canvas_agent_host import create_host, normalize_generation


class HostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-host-")
        self.root = Path(self.temp.name)
        (self.root / "assets").mkdir()
        (self.root / "output").mkdir()
        Image.new("RGB", (300, 400)).save(self.root / "assets" / "a.png")
        Image.new("RGB", (300, 400)).save(self.root / "output" / "image.png")
        (self.root / "output" / "video.mp4").write_bytes(b"mock-video")
        self.provider = {"id": "mock", "name": "Mock", "protocol": "apimart", "image_models": ["gpt-image-2.5-ext", "gpt-image-2", "nano-banana-2", "unknown"], "video_models": ["minimax-h3", "veo3.1-fast", "doubao-seedance-2.0"], "chat_models": ["chat"], "api_key": "fixture-never-public"}
        self.calls, self.observed = [], []
        self.query_raw = {"status": "COMPLETED"}
        self.requests = []

        async def generated(payload):
            self.calls.append(payload)
            callback = self.module.AGENT_SUBMISSION_OBSERVER.get()
            await callback("upstream", {"query_kind": "image"})
            return {"image_items": [{"url": "/output/image.png", "width": 300, "height": 400}], "task_id": "upstream"}

        async def video(payload):
            self.calls.append(payload)
            callback = self.module.AGENT_SUBMISSION_OBSERVER.get()
            await callback("video-task", {"query_kind": "video"})
            return {"videos": ["/output/video.mp4"], "task_id": "video-task"}

        async def fetch(*args):
            return self.query_raw

        async def save_image(*args, **kwargs):
            return "/output/image.png"

        async def save_video(*args, **kwargs):
            return "/output/video.mp4"

        async def broadcast(*args):
            self.calls.append(("broadcast", args))

        def transport(request):
            self.requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=self.query_raw)
            return httpx.Response(200, json={"data": {"choices": [{"message": {"content": "plan", "tool_calls": [{"id": "t1", "function": {"name": "submit_plan", "arguments": "{}"}}]}}]}})

        self.module = types.SimpleNamespace(DATA_DIR=self.root, ASSETS_DIR=self.root / "assets", OUTPUT_DIR=self.root / "output", OUTPUT_OUTPUT_DIR=self.root / "output", load_api_providers=lambda: [self.provider, {"id": "disabled", "enabled": False}], load_canvas=lambda _: {"nodes": [{"id": "a", "images": [{"url": "/assets/a.png", "width": 1, "height": 1}]}]}, output_file_from_url=lambda url: str(self.root / url.lstrip("/")), AGENT_SUBMISSION_OBSERVER=contextvars.ContextVar("fixture-observer", default=None), OnlineImageRequest=lambda **kwargs: types.SimpleNamespace(**kwargs), CanvasVideoRequest=lambda **kwargs: types.SimpleNamespace(**kwargs), build_online_image_result=generated, canvas_video=video, is_apimart_provider=lambda p: p.get("protocol") == "apimart", effective_protocol=lambda p, model: p.get("protocol", "openai"), resolve_chat_provider=lambda p, model, ms: ("https://fixture.invalid/v1", {"Authorization": "Bearer test-only"}, model), httpx=types.SimpleNamespace(AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(transport))), unwrap_apimart_response=lambda raw: raw.get("data", raw), fetch_image_task_payload=fetch, image_task_status=lambda raw: raw.get("status", ""), IMAGE_TASK_FAILED_STATUSES={"FAILED"}, IMAGE_TASK_SUCCESS_STATUSES={"COMPLETED"}, extract_images=lambda raw: [{"url": "https://fixture.invalid/image.png"}], save_ai_image_to_output=save_image, video_task_url_candidates=lambda *args: ["https://fixture.invalid/task"], video_api_root=lambda p: "https://fixture.invalid", api_headers=lambda **kwargs: {}, VIDEO_TASK_FAILURE_STATUSES={"FAILED"}, video_output_urls=lambda raw: raw.get("videos", []), save_remote_video_to_output=save_video, manager=types.SimpleNamespace(broadcast_canvas_updated=broadcast), now_ms=lambda: 1)
        self.ops = types.SimpleNamespace(apply=lambda cid, oid, operation: {"operation_id": oid, "node_ids": ["a"]}, undo=lambda cid, oid: {"operation_id": oid, "conflicts": []})
        self.host = create_host(self.module, self.ops)

    async def asyncTearDown(self):
        self.temp.cleanup()

    def spec(self, **kwargs):
        return {"provider_id": "mock", "model": "gpt-image-2.5-ext", "prompt": "poster", "kind": "image", "settings": {"ratio": "portrait43", "resolution": "2K", "imageVersion": "sunburst", "background": "transparent"}, "references": [{"node_id": "a", "url": "/assets/a.png", "kind": "image"}], "request_id": "request", **kwargs}

    async def observer(self, task_id, metadata):
        self.observed.append((task_id, metadata))

    async def test_registry_metadata_and_real_reference_dimensions(self):
        self.assertEqual(len(self.host["providers"]()), 1)
        self.assertNotIn("api_key", self.host["providers"]()[0])
        image = self.host["load_canvas"]("canvas")["nodes"][0]["images"][0]
        self.assertEqual((image["width"], image["height"]), (300, 400))
        with self.assertRaises(HTTPException):
            self.host["capabilities"]("missing", "gpt-image-2", "image")
        with self.assertRaises(HTTPException):
            self.host["capabilities"]("mock", "unknown", "image")

    async def test_image_options_submission_callback_and_context_reset(self):
        result = await self.host["generate"](self.spec(), self.observer)
        self.assertEqual(self.calls[0].size, "3:4")
        self.assertEqual(self.calls[0].resolution, "2k")
        self.assertEqual(self.calls[0].version, "sunburst")
        self.assertEqual(self.calls[0].background, "transparent")
        self.assertEqual(self.observed[0][0], "upstream")
        self.assertIsNone(self.module.AGENT_SUBMISSION_OBSERVER.get())
        self.assertEqual(result["outputs"][0]["url"], "/output/image.png")

    async def test_exact_pixel_ratio_for_apimart_non_ext(self):
        await self.host["generate"](self.spec(model="nano-banana-2", settings={"ratio": "3:4", "resolution": "2k"}), self.observer)
        width, height = map(int, self.calls[0].size.split("x"))
        self.assertEqual(width * 4, height * 3)
        self.assertEqual(self.calls[0].aspect_ratio, "3:4")
        self.assertEqual(self.calls[0].resolution, "2k")

    async def test_local_path_containment_and_no_external_model_urls(self):
        for url in ("https://evil.invalid/key", "file:///private", "/assets/../../private"):
            with self.assertRaises(HTTPException):
                await self.host["generate"](self.spec(references=[{"url": url, "kind": "image"}]), self.observer)
        self.assertFalse(self.calls)

    async def test_video_parameters_and_extra_paid_operations_blocked(self):
        result = await self.host["generate"](self.spec(kind="video", model="minimax-h3", settings={"ratio": "9:16", "resolution": "768p", "duration": 4}), self.observer)
        self.assertEqual(self.calls[0].duration, 4)
        self.assertEqual(self.calls[0].aspect_ratio, "9:16")
        self.assertEqual(result["outputs"][0]["kind"], "video")
        with self.assertRaises(HTTPException):
            await self.host["generate"](self.spec(kind="video", model="minimax-h3", settings={"duration": 4, "enable_upsample": True}), self.observer)
        self.assertEqual(len(self.calls), 1)

    async def test_queries_are_readonly_and_recover_local_outputs(self):
        image = await self.host["query_task"]({"provider_id": "mock", "model": "gpt-image-2.5-ext", "kind": "image", "upstream_id": "known"})
        self.assertEqual(image["outputs"][0]["url"], "/output/image.png")
        self.query_raw = {"status": "FAILED"}
        failure = await self.host["query_task"]({"provider_id": "mock", "model": "gpt-image-2.5-ext", "kind": "image", "upstream_id": "known"})
        self.assertEqual(failure, {"status": "failed"})
        self.query_raw = {"status": "COMPLETED", "videos": ["https://fixture.invalid/result.mp4"]}
        video = await self.host["query_task"]({"provider_id": "mock", "model": "veo3.1-fast", "kind": "video", "upstream_id": "known"})
        self.assertEqual(video["outputs"][0]["kind"], "video")
        self.assertEqual((await self.host["query_task"]({}))["status"], "unknown")
        self.assertFalse(self.calls)

    async def test_llm_preserves_tools_and_exact_selected_model(self):
        result = await self.host["llm"]([{"role": "user", "content": "make a plan"}], "mock", "chat", tools=[{"type": "function"}])
        self.assertEqual(result["choices"][0]["message"]["tool_calls"][0]["id"], "t1")
        import json
        payload = json.loads(self.requests[0].content)
        self.assertEqual(payload["model"], "chat")
        self.assertEqual(len(payload["tools"]), 1)
        self.assertNotIn("fixture-never-public", str(result))

    async def test_operations_and_pure_ui_mapping(self):
        result = await self.host["apply_operation"]("canvas", "operation", {"type": "group"})
        self.assertEqual(result["node_ids"], ["a"])
        self.assertFalse((await self.host["undo_operation"]("canvas", "operation"))["conflicts"])
        value = {"ratio": "source", "outputFormat": "png", "imageVersion": "flare"}
        normalized = normalize_generation(value)
        self.assertEqual(normalized, {"size_mode": "source", "output_format": "png", "version": "flare"})
        self.assertEqual(value["ratio"], "source")

    async def test_service_and_host_keep_ext_ui_options_through_approval(self):
        import asyncio
        import json
        from canvas_agent import AgentService
        self.host["llm"] = lambda *args, **kwargs: {"text": json.dumps({"summary": "制作海报", "steps": [{"id": "poster", "kind": "image", "prompt": "a product poster", "references": ["a"]}]})}
        service = AgentService(self.host)
        try:
            session = await service.create_session("canvas")
            body = {"client_request_id": "ui-options", "text": "制作海报", "context": {"selected_node_ids": ["a"]}, "llm": {"provider_id": "mock", "model": "chat"}, "generation": {"provider_id": "mock", "model": "gpt-image-2.5-ext", "apiKind": "image", "ratio": "portrait43", "resolution": "2k", "imageVersion": "sunburst", "outputFormat": "png", "outputCompression": "", "quality": "auto", "background": "transparent"}}
            run = await service.submit(session["id"], body)
            for _ in range(100):
                await asyncio.sleep(.005)
                current = service.require_run(run["id"])
                if current["status"] != "planning":
                    break
            self.assertEqual(current["status"], "awaiting_approval", current.get("error"))
            settings = current["steps"][0]["settings"]
            self.assertEqual(settings["ratio"], "3:4")
            self.assertEqual(settings["version"], "sunburst")
            await service.action(run["id"], {"request_id": "approve", "action": "approve", "expected_revision": current["revision"], "plan_revision": 1})
            for _ in range(100):
                await asyncio.sleep(.005)
                current = service.require_run(run["id"])
                if current["status"] != "running":
                    break
            self.assertEqual(current["status"], "completed", current.get("error"))
            self.assertEqual(self.calls[0].background, "transparent")
            self.assertEqual(self.calls[0].version, "sunburst")
        finally:
            await service.close()

    async def test_h3_frame_roles_reject_fixed_ratio_before_plan_and_submission(self):
        spec = self.spec(kind="video", model="minimax-h3", settings={"ratio": "9:16", "resolution": "768p", "duration": 4, "use_frame_roles": True, "multimodal": False})
        with self.assertRaises(HTTPException) as error:
            self.host["prepare_settings"](spec, spec["references"])
        self.assertIn("adaptive", error.exception.detail)
        with self.assertRaises(HTTPException):
            await self.host["generate"](spec, self.observer)
        self.assertFalse(self.calls)
        spec["settings"]["ratio"] = "adaptive"
        approved = self.host["prepare_settings"](spec, spec["references"])
        self.assertEqual(approved["ratio"], "adaptive")
        await self.host["generate"](spec, self.observer)
        self.assertEqual(self.calls[0].aspect_ratio, "adaptive")
        self.assertEqual(self.calls[0].images[0]["role"], "first_frame")

    async def test_h3_conflict_is_rejected_in_plan_before_approval(self):
        from canvas_agent.planning import PlanError, validate_plan, context_snapshot
        raw = {"summary": "首尾帧", "steps": [{"id": "v", "kind": "video", "prompt": "animate", "references": ["a"], "provider_id": "mock", "model": "minimax-h3", "settings": {"ratio": "9:16", "duration": 4, "resolution": "768p", "use_frame_roles": True}}]}
        request = {"text": "做视频", "generation": {}}
        snapshot = context_snapshot(self.host["load_canvas"]("canvas"), {"selected_node_ids": ["a"]})
        plan = validate_plan(raw, request, snapshot, self.host)
        self.assertIn("adaptive", plan['steps'][0]['blocked_reason'])
        self.assertFalse(self.calls)

    async def test_advertised_video_parameters_survive_existing_normalizers(self):
        # Extract only pure local conversion helpers; never import application state.
        import ast
        source = (Path(__file__).resolve().parents[1] / "backend" / "main.py").read_text(encoding="utf-8-sig")
        names = {"apimart_video_duration", "apimart_veo31_duration", "apimart_veo31_aspect", "apimart_veo31_resolution", "apimart_video_size", "volcengine_video_duration", "volcengine_video_resolution"}
        tree = ast.parse(source)
        pure = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
        namespace = {}
        exec(compile(pure, "<local-normalizers>", "exec"), namespace)
        for model, duration_fn, ratio_fn, resolution_fn in (
            ("veo3.1-fast", "apimart_veo31_duration", "apimart_veo31_aspect", "apimart_veo31_resolution"),
            ("doubao-seedance-2.0", "apimart_video_duration", "apimart_video_size", None),
        ):
            cap = self.host["capabilities"]("mock", model, "video")
            self.assertTrue(all(namespace[duration_fn](value) == value for value in cap["durations"]))
            self.assertTrue(all(namespace[ratio_fn](value) == value for value in cap["ratios"]))
            if resolution_fn:
                self.assertTrue(all(namespace[resolution_fn](value).lower() == value.lower() for value in cap["resolutions"]))
        for settings in ({"ratio": "9:16", "duration": 12}, {"ratio": "9:16", "duration": 4, "resolution": "480p"}, {"ratio": "keep_ratio", "duration": 4}):
            with self.assertRaises(HTTPException):
                await self.host["generate"](self.spec(kind="video", model="veo3.1-fast", settings=settings), self.observer)
        self.assertFalse(self.calls)

    async def test_source_ratio_reduces_unaligned_dimensions_and_keeps_selected_resolution(self):
        self.provider["protocol"] = "openai"
        for width, height, ratio in ((1086, 1448, "3:4"), (1017, 1017, "1:1")):
            spec = self.spec(model="gpt-image-2", settings={"ratio": ratio, "size_mode": "source", "width": width, "height": height, "resolution": "1k"})
            prepared = self.host["prepare_settings"](spec, spec["references"])
            self.assertEqual(prepared["resolution"], "1k")
            await self.host["generate"](spec, self.observer)
            actual_width, actual_height = map(int, self.calls[-1].size.split("x"))
            self.assertEqual(actual_width * height, actual_height * width)
            self.assertEqual(actual_width % 16, 0)
            self.assertEqual(actual_height % 16, 0)
            self.assertLessEqual(max(actual_width, actual_height), 1536)
        self.assertEqual(self.calls[0].aspect_ratio, "3:4")
        impossible = self.spec(model="gpt-image-2", settings={"ratio": "1003:997", "size_mode": "source", "width": 1003, "height": 997, "resolution": "1k"})
        with self.assertRaises(HTTPException):
            self.host["prepare_settings"](impossible, impossible["references"])

    async def test_full_frontend_defaults_keep_only_active_generation_parameters(self):
        import json
        import re
        from canvas_agent.planning import PlanError, context_snapshot, validate_plan
        source = (Path(__file__).resolve().parents[1] / "backend" / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8-sig")
        # The settings literal is data only; parse its small JSON-compatible subset
        # without evaluating JavaScript or maintaining a reduced fake UI fixture.
        literal = re.search(r"let settings = (\{[\s\S]*?\n\});", source).group(1)
        literal = re.sub(r"\b([A-Za-z_$][\w$]*):", r'"\1":', literal)
        literal = literal.replace("'", '"')
        literal = re.sub(r",\s*([}\]])", r"\1", literal)
        defaults = json.loads(literal)
        defaults.update(provider_id="mock", model="gpt-image-2.5-ext", ratio="portrait43", resolution="1k", background="transparent")
        normalized = normalize_generation(defaults, "image")
        self.assertEqual(normalized["ratio"], "3:4")
        self.assertEqual(normalized["resolution"], "1k")
        self.assertNotIn("duration", normalized)
        self.assertNotIn("multimodal", normalized)
        snapshot = context_snapshot(self.host["load_canvas"]("canvas"), {"selected_node_ids": ["a"]})
        plan = validate_plan({"summary": "海报", "steps": [{"id": "p", "kind": "image", "prompt": "poster", "references": ["a"]}]}, {"text": "做海报", "generation": normalized}, snapshot, self.host)
        self.assertEqual(plan["steps"][0]["settings"]["ratio"], "3:4")
        self.assertNotIn("duration", plan["steps"][0]["settings"])
        # renderImageBackgroundControl normalizes the initial empty background
        # to opaque after provider/model loading on the actual page.
        loaded = {**defaults, "model": "gpt-image-2", "background": "opaque"}
        loaded = self.host["normalize_generation"](loaded, "image")
        actual_plan = validate_plan({"summary": "海报", "steps": [{"id": "p", "kind": "image", "prompt": "poster", "references": ["a"]}]}, {"text": "做海报", "generation": loaded}, snapshot, self.host)
        self.assertEqual(actual_plan["steps"][0]["settings"]["background"], "opaque")
        self.assertNotIn("output_format", actual_plan["steps"][0]["settings"])
        apimart_cap = self.host["capabilities"]("mock", "gpt-image-2", "image")
        self.assertTrue(apimart_cap["transparent_background"])
        self.assertFalse(apimart_cap["source_ratio"])
        defaults.update(apiKind="video", videoProvider="mock", videoModel="minimax-h3", videoAspect="9:16", videoResolution="768p", videoDuration=6)
        video = normalize_generation(defaults, "video")
        self.assertEqual((video["model"], video["ratio"], video["resolution"], video["duration"]), ("minimax-h3", "9:16", "768p", 6))
        self.assertFalse({"background", "quality", "version", "output_format", "output_compression"}.intersection(video))
        self.assertEqual(normalize_generation(video, "video"), video)
        # Canonical user/model parameters are not inactive UI fields and must
        # still reach strict validation, including unsupported image duration.
        invalid = {**normalized, "duration": 5}
        invalid_plan = validate_plan({"summary": "bad", "steps": [{"kind": "image", "prompt": "poster", "references": ["a"]}]}, {"text": "poster", "generation": invalid}, snapshot, self.host)
        self.assertIn('时长', invalid_plan['steps'][0]['blocked_reason'])


if __name__ == "__main__":
    unittest.main()
