"""Narrow bridge from the durable assistant to the existing desktop adapters.

Provider configuration is resolved only at call time. This module never stores keys,
changes global generation settings, or follows media locations suggested by an LLM.
"""
import asyncio
import copy
import inspect
import json
import math
from pathlib import Path
import re

import httpx
from fastapi import HTTPException


RATIO_NAMES = {"square": "1:1", "portrait": "2:3", "landscape": "3:2", "portrait43": "3:4", "landscape43": "4:3", "story": "9:16", "wide": "16:9", "ultrawide": "21:9", "ultratall": "9:21"}
RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9", "9:21", "5:4", "4:5"]
EXT_RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"]
SETTING_ALIASES = {"imageVersion": "version", "outputFormat": "output_format", "outputCompression": "output_compression", "sizeMode": "size_mode", "videoDuration": "duration", "videoAspect": "ratio", "videoResolution": "resolution", "videoEnhancePrompt": "enhance_prompt", "videoEnableUpsample": "enable_upsample", "videoWatermark": "watermark", "videoCameraFixed": "camerafixed", "videoGenerateAudio": "generate_audio", "videoMultimodal": "multimodal", "videoUseFrameRoles": "use_frame_roles", "videoReturnLastFrame": "return_last_frame", "videoTrustedAsset": "trusted_asset"}


async def _await(value):
    return await value if inspect.isawaitable(value) else value


def normalize_generation(settings, kind=None):
    """Pure conversion of UI names; never choose a different model or ratio."""
    raw = settings or {}
    active_kind = kind or raw.get("apiKind")
    ui_snapshot = "engine" in raw or raw.get("apiKind") in ("image", "video")
    active = {}
    for key, value in raw.items():
        if active_kind == "image" and key.startswith(("video", "_video")):
            continue
        if active_kind == "video":
            if key in ("imageVersion", "outputFormat", "outputCompression"):
                continue
            if ui_snapshot and key in ("background", "quality", "customRatio", "customRatioWidth", "customRatioHeight", "customSize", "customWidth", "customHeight", "width", "height"):
                continue
            if (key == "ratio" and "videoAspect" in raw) or (key == "resolution" and "videoResolution" in raw):
                continue
        active[key] = value
    result = {SETTING_ALIASES.get(k, k): copy.deepcopy(v) for k, v in active.items()}
    if active_kind == "video":
        if result.get("videoProvider"):
            result["provider_id"] = result["videoProvider"]
        if result.get("videoModel"):
            result["model"] = result["videoModel"]
    if result.get("ratio") == "source":
        result["size_mode"] = "source"
        result.pop("ratio")
    elif "ratio" in result:
        result["ratio"] = RATIO_NAMES.get(result["ratio"], result["ratio"])
    if result.get("resolution"):
        result["resolution"] = str(result["resolution"]).lower()
    if ui_snapshot:
        for key in ("background", "quality", "output_format", "output_compression"):
            if result.get(key) in (None, ""):
                result.pop(key, None)
    # These UI controls remain in the global settings object even while hidden
    # for another model. Their inactive defaults are not model instructions.
    model = str(result.get("model", "")).lower()
    if "imageVersion" in (settings or {}) and model and model != "gpt-image-2.5-ext":
        result.pop("version", None)
    if model and (not model.startswith("gpt-image-2") or model == "gpt-image-2.5-ext"):
        if "outputFormat" in (settings or {}) and result.get("output_format") == "png":
            result.pop("output_format", None)
        if result.get("background") == "":
            result.pop("background", None)
    return result


def create_host(module, canvas_ops):
    m = module

    def registry():
        return [p for p in m.load_api_providers() if p.get("enabled", True)]

    def provider(provider_id, model, kind):
        selected = next((p for p in registry() if str(p.get("id")) == str(provider_id)), None)
        if not selected or model not in selected.get(kind + "_models", []):
            raise HTTPException(422, "所选平台或模型未配置，不能自动替换。")
        return selected

    def providers():
        return [{k: copy.deepcopy(p.get(k, [] if k.endswith("_models") else "")) for k in ("id", "name", "chat_models", "image_models", "video_models")} for p in registry()]

    def flag(name, p):
        fn = getattr(m, name, None)
        return bool(fn(p)) if fn else False

    def capabilities(provider_id, model, kind):
        p = provider(provider_id, model, kind)
        name = str(model).strip().lower().replace("_", "-")
        apimart = flag("is_apimart_provider", p)
        protocol = m.effective_protocol(p, model) if hasattr(m, "effective_protocol") else p.get("protocol", "openai")
        cli = flag("is_codex_provider", p) or flag("is_gemini_cli_provider", p)
        cap = {"available": True, "ratios": [], "resolutions": [], "durations": [], "max_references": 0, "source_ratio": False, "auto_ratio": False, "recoverable": False, "cancellable": False, "settings": {}}
        if kind == "image":
            if name == "gpt-image-2.5-ext" and apimart:
                cap.update(ratios=EXT_RATIOS + ["auto"], resolutions=["1k", "2k", "4k"], max_references=16, auto_ratio=True, recoverable=True, transparent_background=True)
                cap["settings"] = {"version": ["flare", "sunburst"], "background": ["auto", "opaque", "transparent"]}
            elif name in ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gpt-image-2-5-flare", "gpt-image-2-5-sunburst") and apimart:
                cap.update(ratios=RATIOS + ["auto"], resolutions=["1k", "2k", "4k"], max_references=16, auto_ratio=True, recoverable=True, transparent_background=True)
                cap["settings"] = {"quality": ["auto", "low", "medium", "high"], "background": ["auto", "opaque", "transparent"], "output_format": ["png", "jpeg", "webp"], "output_compression": list(range(101))}
            elif name in ("gpt-image-2", "gpt-image-2-official", "gpt-image-2-2026-04-21"):
                cap.update(ratios=RATIOS + ["auto"], resolutions=["0.5k", "1k", "2k", "4k"], max_references=15 if apimart else int(getattr(m, "ONLINE_IMAGE_REFERENCE_MAX", 16)), source_ratio=not apimart and not cli and protocol == "openai", auto_ratio=True, recoverable=apimart)
                cap["settings"] = {"quality": ["auto", "low", "medium", "high"]}
                cap["pixel_limits"] = {"max_edge": int(getattr(m, "GPT_IMAGE2_MAX_EDGE", 3840)), "max_pixels": int(getattr(m, "GPT_IMAGE2_MAX_PIXELS", 8_294_400)), "min_pixels": int(getattr(m, "GPT_IMAGE2_MIN_PIXELS", 655_360))}
                cap["resolutions"] = ["1k", "2k", "4k"]
                if apimart:
                    cap["resolutions"] = ["1k", "2k", "4k"]
                if not cli and (protocol == "openai" or apimart):
                    cap["transparent_background"] = True
                    cap["settings"].update(background=["auto", "opaque", "transparent"])
                    if not apimart or name == "gpt-image-2-official":
                        cap["settings"]["output_format"] = ["png", "jpeg", "webp"]
            elif name in ("nano-banana", "nano-banana-2", "nano-banana-2-ext", "nano-banana-pro", "gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview", "gemini-2.5-flash-image"):
                cap.update(ratios=RATIOS, resolutions=["1k", "2k", "4k"], max_references=14 if apimart else int(getattr(m, "ONLINE_IMAGE_REFERENCE_MAX", 16)), recoverable=apimart)
            elif "seedream" in name:
                cap.update(ratios=RATIOS, resolutions=["2k", "4k"], max_references=int(getattr(m, "ONLINE_IMAGE_REFERENCE_MAX", 16)), recoverable=apimart)
            else:
                raise HTTPException(422, "该图片模型尚未建立 Agent 参数契约，请使用现有手动生成入口。")
        elif kind == "video":
            if apimart and name == "minimax-h3":
                cap.update(ratios=["21:9", "16:9", "4:3", "1:1", "3:4", "9:16", "adaptive"], resolutions=["768p", "2k"], durations=list(range(4, 16)), max_references=9, recoverable=True)
            elif apimart and name in ("veo3.1-fast", "veo3.1-quality", "veo3.1-lite"):
                cap.update(ratios=["16:9", "9:16"], resolutions=["720p", "1080p", "4k"], durations=list(range(4, 9)), max_references=0 if name.endswith("lite") else 2 if name.endswith("quality") else 3, recoverable=True)
            elif "seedance" in name and apimart:
                cap.update(ratios=["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"], resolutions=["480p", "720p", "1080p"], durations=list(range(4, 16)), max_references=9, recoverable=True)
            elif name in ("veo3", "veo3-fast", "veo2", "veo2-fast") and not any(flag(f, p) for f in ("is_yuli_provider", "is_tudou_provider", "is_lingjing_provider", "is_agnes_provider", "is_jimeng_provider")):
                cap.update(ratios=["16:9", "9:16"], resolutions=["720p", "1080p"], durations=[4, 5, 6, 8], max_references=3, recoverable=True)
            else:
                raise HTTPException(422, "该视频模型尚未建立 Agent 参数契约，请使用现有手动生成入口。")
            cap["settings"] = {k: [False] for k in ("watermark", "return_last_frame", "generate_audio", "multimodal", "use_frame_roles", "camerafixed", "trusted_asset")}
            if apimart and name == "minimax-h3":
                for key in ("return_last_frame", "generate_audio", "multimodal", "use_frame_roles", "trusted_asset"):
                    cap["settings"][key] = [False, True]
            elif "seedance" in name:
                for key in ("generate_audio", "use_frame_roles", "trusted_asset"):
                    cap["settings"][key] = [False, True]
                cap["settings"]["return_last_frame"] = [False, True]
            elif not apimart:
                for key in ("watermark", "return_last_frame", "generate_audio", "camerafixed"):
                    cap["settings"][key] = [False, True]
            # H3 enhancement/upscale may introduce additional paid submissions. Keep
            # these outside a one-generation approval until separately budgeted.
            cap["settings"].update(enhance_prompt=[False], enable_upsample=[False])
            cap["reference_kinds"] = ["image", "video", "audio"] if "seedance" in name or name == "minimax-h3" else ["image"]
        else:
            raise HTTPException(422, "任务类型无效。")
        return cap

    def normalize_for_host(settings, kind=None):
        result = normalize_generation(settings, kind)
        raw = settings or {}
        if ("engine" in raw or raw.get("apiKind") == "image") and (kind or raw.get("apiKind")) == "image":
            try:
                cap = capabilities(result.get("provider_id"), result.get("model"), "image")
            except HTTPException:
                return result  # A discussion can still use an unadapted selected model.
            # A PNG default retained by the shared UI is inactive on adapters
            # without a format option. Canonical plan parameters remain strict.
            if "outputFormat" in raw and result.get("output_format") == "png" and "output_format" not in cap["settings"]:
                result.pop("output_format", None)
        return result

    def local_file(url):
        if not isinstance(url, str) or not url.startswith(("/output/", "/assets/", "/api/storage-files/")):
            raise HTTPException(422, "Agent 仅能使用当前数据目录中已保存的素材，请先导入远程素材。")
        path = m.output_file_from_url(url)
        if not path:
            resolver = getattr(m, "asset_path_from_reference", None)
            path = resolver(url) if resolver else None
        if not path:
            raise HTTPException(422, "引用素材文件不存在。")
        candidate = Path(path).resolve()
        roots = [Path(getattr(m, key)).resolve() for key in ("DATA_DIR", "ASSETS_DIR", "OUTPUT_DIR", "OUTPUT_OUTPUT_DIR", "OUTPUT_INPUT_DIR", "OUTPUT_TEMP_DIR") if getattr(m, key, None)]
        if not candidate.is_file() or not any(candidate.is_relative_to(root) for root in roots):
            raise HTTPException(422, "引用素材不在当前允许的数据目录。")
        return candidate

    def load_canvas(canvas_id):
        canvas = copy.deepcopy(m.load_canvas(canvas_id))
        for node in canvas.get("nodes", []):
            for item in node.get("images", []):
                if not isinstance(item, dict) or not item.get("url") or item.get("kind") in ("video", "audio"):
                    continue
                try:
                    path = local_file(item["url"])
                    from PIL import Image
                    with Image.open(path) as image:
                        item["width"], item["height"] = image.size
                except (HTTPException, OSError, ValueError):
                    # No remote fetch to inspect context. Generation revalidates file access.
                    item.pop("width", None)
                    item.pop("height", None)
        return canvas

    async def llm(messages, provider_id, model, tools=None):
        p = provider(provider_id, model, "chat")
        if flag("is_codex_provider", p) or flag("is_gemini_cli_provider", p):
            if tools:
                raise HTTPException(400, "tools unsupported for configured CLI adapter")
            payload = m.CanvasLLMRequest(provider=provider_id, model=model, system_prompt="\n".join(str(x.get("content", "")) for x in messages if x.get("role") == "system"), messages=[x for x in messages[:-1] if x.get("role") in ("user", "assistant")], message=str(messages[-1].get("content", "")))
            result = await m.canvas_llm(payload)
            return {"text": str(result.get("text", ""))}
        base, headers, resolved = m.resolve_chat_provider(provider_id, model, model if provider_id == "modelscope" else "")
        if resolved != model:
            raise HTTPException(422, "平台未使用所选模型，已阻止请求。")
        protocol = m.effective_protocol(p, model) if hasattr(m, "effective_protocol") else p.get("protocol", "openai")
        if protocol == "gemini":
            if tools:
                raise HTTPException(400, "tools unsupported for configured native Gemini adapter")
            body = {
                "contents": [
                    {"role": "model" if x.get("role") == "assistant" else "user", "parts": [{"text": str(x.get("content", ""))}]}
                    for x in messages if x.get("role") in ("user", "assistant")
                ],
                "systemInstruction": {"parts": [{"text": "\n".join(str(x.get("content", "")) for x in messages if x.get("role") == "system")}]},
            }
            endpoint = base.rstrip("/") + "/models/" + model + ":generateContent"
        else:
            body = {"model": model, "messages": copy.deepcopy(messages), "stream": False}
            if tools:
                body["tools"] = tools
            endpoint = base.rstrip("/") + "/chat/completions"
        try:
            async with m.httpx.AsyncClient(timeout=getattr(m, "AI_REQUEST_TIMEOUT", 120)) as client:
                response = await client.post(endpoint, headers=headers, json=body)
                if response.status_code >= 400:
                    text = response.text.lower()
                    if tools and response.status_code in (400, 422) and "tool" in text and any(word in text for word in ("unsupported", "not support", "unknown parameter")):
                        raise HTTPException(400, "tools unsupported by configured model")
                    raise HTTPException(response.status_code, "所选模型请求未完成，请检查平台配置。")
                raw = response.json()
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(502, "所选模型连接或响应异常，请稍后重试。") from None
        if protocol == "gemini":
            parts = ((raw.get("candidates") or [{}])[0].get("content") or {}).get("parts", [])
            return {"text": "\n".join(p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought"))}
        raw = m.unwrap_apimart_response(raw) if hasattr(m, "unwrap_apimart_response") else raw
        if not isinstance(raw, dict) or not raw.get("choices"):
            raise HTTPException(502, "所选模型未返回可用回复。")
        msg = raw["choices"][0].get("message", {})
        content = msg.get("content", "")
        if isinstance(content, list):
            content = "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") in ("text", "output_text"))
        return {"choices": [{"message": {"role": "assistant", "content": content, "tool_calls": copy.deepcopy(msg.get("tool_calls", []))}}]}

    def image_size(settings, cap):
        ratio = settings.get("ratio", "1:1")
        if ratio == "auto":
            return "auto"
        if settings.get("width") and settings.get("height") and settings.get("size_mode") == "source":
            width, height = int(settings["width"]), int(settings["height"])
            if width <= 0 or height <= 0:
                raise HTTPException(422, "参考图尺寸无法由此接口精确表达，请选择固定比例。")
            # Source describes aspect, not an instruction to preserve the raw
            # input resolution. Reduce first, then fit the selected resolution.
            a, b = width, height
        else:
            a, b = map(int, ratio.split(":"))
        divisor = math.gcd(a, b)
        a, b = a // divisor, b // divisor
        target = {"0.5k": 512, "1k": 1536, "2k": 2048, "4k": 3840}.get(settings.get("resolution", "1k"), 1536)
        scale = target // (max(a, b) * 16)
        limits = cap.get("pixel_limits", {})
        if limits:
            scale = min(scale, limits["max_edge"] // (max(a, b) * 16), math.isqrt(limits["max_pixels"] // (a * b * 256)))
        if settings.get("resolution", "1k") == "1k":
            while scale > 1 and a * b * scale * scale * 256 > 1_800_000:
                scale -= 1
        if scale < 1 or (limits and a * b * scale * scale * 256 < limits["min_pixels"]):
            raise HTTPException(422, "此输入比例无法在所选分辨率和模型像素限制内精确表达，请选择支持的固定比例。")
        return f"{a * scale * 16}x{b * scale * 16}"

    def validated(spec, deferred_references=False):
        cap = capabilities(spec["provider_id"], spec["model"], spec["kind"])
        settings = normalize_generation(spec.get("settings", {}), spec["kind"])
        if len(spec.get("references", [])) > cap["max_references"]:
            raise HTTPException(422, "参考素材数量超过模型上限。")
        for reference in spec.get("references", []):
            local_file(reference.get("url"))
            if reference.get("kind", "image") not in cap.get("reference_kinds", ["image"]):
                raise HTTPException(422, "模型不支持此类参考素材。")
        for key, plural in (("ratio", "ratios"), ("resolution", "resolutions"), ("duration", "durations")):
            value = settings.get(key)
            if value not in (None, "") and value not in cap[plural]:
                if key == "ratio" and cap["source_ratio"] and settings.get("size_mode") == "source" and re.fullmatch(r"[1-9]\d*:[1-9]\d*", str(value)):
                    continue
                raise HTTPException(422, "所选生成参数不受模型支持，已阻止提交。")
        for key, values in cap["settings"].items():
            if key in settings and settings[key] not in (None, "") and settings[key] not in values:
                raise HTTPException(422, "所选生成选项不受模型支持，已阻止提交。")
        for key in ("enhance_prompt", "enable_upsample"):
            if settings.get(key):
                raise HTTPException(422, "提示词增强或升采样涉及额外任务，请使用手动入口单独确认。")
        if spec["kind"] == "video":
            p = provider(spec["provider_id"], spec["model"], "video")
            name = str(spec["model"]).lower()
            refs = spec.get("references", [])
            if settings.get("use_frame_roles"):
                if flag("is_apimart_provider", p) and name == "minimax-h3" and settings.get("ratio") != "adaptive":
                    raise HTTPException(422, "MiniMax-H3 首尾帧模式必须明确选择 adaptive 比例；固定比例不会被自动改写。")
                if settings.get("multimodal"):
                    raise HTTPException(422, "首尾帧模式不能同时启用全能参考。")
                images = [r for r in refs if r.get("kind", "image") == "image"]
                if len(images) > 2 or any(r.get("kind", "image") != "image" for r in refs) or (not images and not deferred_references):
                    raise HTTPException(422, "首尾帧模式需要 1～2 张图片，不能混用视频或音频。")
            if flag("is_apimart_provider", p) and name == "minimax-h3" and settings.get("ratio") == "adaptive" and not refs and not deferred_references:
                raise HTTPException(422, "MiniMax-H3 adaptive 比例需要参考素材，请明确选择固定比例。")
        return cap, settings

    def prepare_settings(step, references):
        _, settings = validated({**step, "references": references}, deferred_references=any("step_id" in r for r in step.get("references", [])))
        if step["kind"] == "image":
            image_size(settings, capabilities(step["provider_id"], step["model"], "image"))
        return settings

    def outputs_from_result(result, kind):
        records = result.get("image_items") if kind == "image" else result.get("videos")
        records = (records or result.get("images", [])) if kind == "image" else (records or [])
        outputs = []
        for record in records:
            record = {"url": record} if isinstance(record, str) else record
            if not isinstance(record, dict) or not record.get("url"):
                continue
            local_file(record["url"])
            outputs.append({"url": record["url"], "kind": kind, **{k: record[k] for k in ("width", "height", "name") if k in record}})
        if kind == "video":
            for record in result.get("images", []):
                record = {"url": record} if isinstance(record, str) else record
                if isinstance(record, dict) and record.get("url"):
                    local_file(record["url"])
                    outputs.append({"url": record["url"], "kind": "image", **{k: record[k] for k in ("width", "height", "name") if k in record}})
        return {"outputs": outputs, **({"upstream_id": result["task_id"]} if result.get("task_id") else {})}

    async def generate(spec, on_submitted):
        cap, settings = validated(spec)
        refs = [{"url": r["url"], "kind": r.get("kind", "image"), "name": str(r.get("name", ""))} for r in spec.get("references", [])]
        observer = m.AGENT_SUBMISSION_OBSERVER
        token = observer.set(on_submitted)
        try:
            if spec["kind"] == "image":
                p = provider(spec["provider_id"], spec["model"], "image")
                size = image_size(settings, cap)
                # Ext accepts a closed ratio enum. Other adapters derive their
                # ratio from exact-ratio pixel dimensions (never rounded dimensions).
                if flag("is_apimart_provider", p) and str(spec["model"]).lower() == "gpt-image-2.5-ext":
                    size = settings.get("ratio", "1:1")
                body = {"provider_id": spec["provider_id"], "model": spec["model"], "prompt": spec["prompt"], "n": 1, "size": size, "aspect_ratio": settings.get("ratio", ""), "resolution": settings.get("resolution", ""), "reference_images": refs}
                for key in ("quality", "background", "version", "output_format", "output_compression"):
                    if key in settings:
                        if key not in cap["settings"]:
                            if settings[key] in (None, "", "auto"):
                                continue
                            raise HTTPException(422, "此模型不支持所选图片选项。")
                        body[key] = settings[key]
                if settings.get("transparent_background"):
                    if not cap.get("transparent_background"):
                        raise HTTPException(422, "模型未声明支持透明背景。")
                    body["background"] = "transparent"
                result = await m.build_online_image_result(m.OnlineImageRequest(**body))
            else:
                body = {"provider_id": spec["provider_id"], "model": spec["model"], "prompt": spec["prompt"], "duration": settings.get("duration", cap["durations"][0]), "aspect_ratio": settings.get("ratio", cap["ratios"][0]), "resolution": settings.get("resolution", ""), "images": [r for r in refs if r["kind"] == "image"], "videos": [r["url"] for r in refs if r["kind"] == "video"], "audios": [r["url"] for r in refs if r["kind"] == "audio"]}
                body.update({k: settings[k] for k in cap["settings"] if k in settings})
                if body.get("use_frame_roles"):
                    for i, reference in enumerate(body["images"]):
                        reference["role"] = "first_frame" if i == 0 else "last_frame"
                result = await m.canvas_video(m.CanvasVideoRequest(**body))
            return outputs_from_result(result, spec["kind"])
        finally:
            observer.reset(token)

    async def query_task(task):
        task_id = task.get("upstream_id")
        if not task_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,239}", str(task_id)):
            return {"status": "unknown"}
        kind = task.get("recovery", {}).get("query_kind", task.get("kind"))
        p = provider(task["provider_id"], task["model"], kind)
        cap = capabilities(task["provider_id"], task["model"], kind)
        if not cap["recoverable"]:
            return {"status": "unknown"}
        async with m.httpx.AsyncClient(timeout=getattr(m, "AI_REQUEST_TIMEOUT", 120)) as client:
            if kind == "image":
                raw = await m.fetch_image_task_payload(client, task_id, p)
                status = m.image_task_status(raw)
                if status in m.IMAGE_TASK_FAILED_STATUSES:
                    return {"status": "failed"}
                if status and status not in m.IMAGE_TASK_SUCCESS_STATUSES:
                    return {"status": "running"}
                try:
                    images = m.extract_images(raw)
                except HTTPException:
                    return {"status": "running"}
                records = [{"url": await m.save_ai_image_to_output(item, prefix="agent_recovered_")} for item in images]
                return outputs_from_result({"image_items": records, "task_id": task_id}, "image")
            urls = m.video_task_url_candidates(p, m.video_api_root(p), task_id)
            raw = None
            for url in urls:
                response = await client.get(url, headers=m.api_headers(provider=p))
                if response.status_code in (404, 405):
                    continue
                response.raise_for_status()
                raw = response.json()
                break
            if not isinstance(raw, dict):
                return {"status": "unknown"}
            data = raw.get("data") if isinstance(raw.get("data"), dict) else raw.get("detail") if isinstance(raw.get("detail"), dict) else raw
            status = str(data.get("status") or data.get("task_status") or raw.get("status") or "").upper()
            if status in m.VIDEO_TASK_FAILURE_STATUSES:
                return {"status": "failed"}
            found = m.video_output_urls(raw)
            if not found:
                return {"status": "running"}
            records = [await m.save_remote_video_to_output(url) for url in found]
            return outputs_from_result({"videos": records, "task_id": task_id}, "video")

    async def broadcast(canvas_id):
        canvas = m.load_canvas(canvas_id)
        await m.manager.broadcast_canvas_updated(canvas_id, int(canvas.get("updated_at") or m.now_ms()), "canvas-agent")

    async def apply_operation(canvas_id, operation_id, operation):
        return await _await(canvas_ops.apply(canvas_id, operation_id, operation))

    async def undo_operation(canvas_id, operation_id):
        return await _await(canvas_ops.undo(canvas_id, operation_id))

    return {"active_task_registry": getattr(m, "CANVAS_TASKS", None), "data_dir": str(m.DATA_DIR), "providers": providers, "load_canvas": load_canvas, "capabilities": capabilities, "llm": llm, "generate": generate, "query_task": query_task, "apply_operation": apply_operation, "undo_operation": undo_operation, "broadcast": broadcast, "normalize_generation": normalize_for_host, "prepare_settings": prepare_settings}
