"""APIMart 2.6 Ext contract and safe, format-preserving image handling."""
import base64
from io import BytesIO
import math
import re
import xml.etree.ElementTree as ET
import json
import os
from pathlib import Path
import threading
import time
import uuid

from fastapi import HTTPException
from PIL import Image

MODEL = "gpt-image-2.6-ext"
RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9")
QUALITIES = ("auto", "low", "medium", "high", "xhigh", "max")
FORMATS = ("png", "jpeg", "webp", "svg", "gif")
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
MAX_REFERENCE_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_BYTES = 40 * 1024 * 1024


class TaskJournal:
    """Keep task identities and downloaded results across restarts, without credentials."""
    def __init__(self, root):
        self.root = Path(root) / 'image26-tasks'
        self.lock = threading.RLock()

    def path(self, task_id):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,240}', str(task_id)):
            raise HTTPException(400, '任务号无效。')
        import hashlib
        return self.root / (hashlib.sha256(str(task_id).encode()).hexdigest() + '.json')

    def get(self, task_id):
        with self.lock:
            path = self.path(task_id)
            return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def update(self, task_id, **values):
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            record = self.get(task_id) or {'task_id': task_id, 'created_at': time.time()}
            record.update(values)
            path = self.path(task_id)
            temporary = path.with_suffix('.' + uuid.uuid4().hex + '.part')
            try:
                temporary.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            return record


def is_model(model):
    return str(model or "").strip().lower() == MODEL


def closest_ratio(width, height):
    if not width or not height or width <= 0 or height <= 0:
        raise HTTPException(400, "无法读取适配图片尺寸，请重新选择参考图。")
    ratio = width / height
    return min(RATIOS, key=lambda item: abs(ratio / ratio_value(item) - 1))


def ratio_value(value):
    width, height = map(float, value.split(":"))
    return width / height


def normalize(size="auto", resolution="", version="flare", quality="", output_format="", complexity="", svg=None, reference_count=0):
    def choice(value, allowed, label, default=""):
        value = str(value or default).strip().lower()
        if value and value not in allowed:
            raise HTTPException(400, f"GPT-Image-2.6 Ext {label}不支持 {value}")
        return value

    if reference_count > 16:
        raise HTTPException(400, "GPT-Image-2.6 Ext 最多支持 16 张参考图，请移除超出的图片。")
    resolution = choice(resolution, ("1k",), "当前分辨率仅开放 1K，", "1k")
    body = {"model": MODEL, "version": choice(version, ("flare", "sunburst"), "版本", "flare"),
            "resolution": resolution, "output_format": choice("jpeg" if str(output_format).lower() == "jpg" else output_format, FORMATS, "输出格式", "png"), "n": 1}
    raw_size = re.sub(r"\s+", "", str(size or "auto").lower())
    pixels = re.fullmatch(r"(\d+)[x*](\d+)", raw_size)
    if pixels:
        width, height = map(int, pixels.groups())
        raw_size = closest_ratio(width, height)
    if raw_size not in ("auto", *RATIOS):
        raise HTTPException(400, "GPT-Image-2.6 Ext 尺寸请使用自动或支持的 10 种比例。")
    body["size"] = raw_size
    for key, value, allowed in (("quality", quality, QUALITIES), ("complexity", complexity, ("low", "medium", "high"))):
        value = choice(value, allowed, key)
        if value:
            body[key] = value
    if body["output_format"] in ("svg", "gif") and reference_count:
        raise HTTPException(400, "SVG / GIF 仅支持文生图，请移除参考图或改选 PNG / JPEG / WebP。")
    if body["output_format"] == "gif" and raw_size != "auto":
        raise HTTPException(400, "GIF 仅支持自动比例，请选择自动尺寸。")
    if svg:
        if body["output_format"] != "svg" or not isinstance(svg, dict) or set(svg) - {"model", "reasoning"}:
            raise HTTPException(400, "SVG 编写配置仅适用于 SVG 输出。")
        model = str(svg.get("model") or "").strip()
        if not model or len(model) > 160 or re.search(r"[\x00-\x1f]", model):
            raise HTTPException(400, "请填写 SVG 编写模型。")
        body["svg"] = {"model": model}
        if svg.get("reasoning"):
            reasoning = svg["reasoning"]
            if not isinstance(reasoning, dict) or set(reasoning) != {"effort"}:
                raise HTTPException(400, "SVG 推理配置仅支持 effort。")
            body["svg"]["reasoning"] = {"effort": choice(reasoning["effort"], EFFORTS, "SVG 推理档位")}
    return body


def reference_data_url(ref, resolve):
    value = str(ref.get("url") or "").strip()
    if value.startswith(("https://", "http://")):
        return value
    if value.startswith("data:image/") and ";base64," in value:
        try:
            raw = base64.b64decode(value.split(";base64,", 1)[1], validate=True)
        except ValueError:
            raise HTTPException(400, "参考图 Base64 无效。") from None
    else:
        path = resolve(value)
        if not path:
            raise HTTPException(400, "参考图文件不存在，请重新添加。")
        from pathlib import Path
        if Path(path).stat().st_size > MAX_REFERENCE_BYTES:
            raise HTTPException(400, "参考图超过 20 MB，请压缩后重试。")
        raw = Path(path).read_bytes()
        if str(path).lower().endswith(".svg"):
            raw = raster_svg(raw)
    if len(raw) > MAX_REFERENCE_BYTES:
        raise HTTPException(400, "参考图超过 20 MB，请压缩后重试。")
    try:
        with Image.open(BytesIO(raw)) as image:
            fmt = image.format
            image.verify()
        mime = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp", "GIF": "gif", "BMP": "bmp"}[fmt]
    except (OSError, ValueError, KeyError):
        raise HTTPException(400, "参考图须为有效 PNG / JPEG / WebP / GIF / BMP。") from None
    return f"data:image/{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def clean_svg(raw):
    if len(raw) > MAX_OUTPUT_BYTES or b"<!doctype" in raw.lower() or b"<!entity" in raw.lower():
        raise ValueError("SVG 文档过大或包含不允许的实体声明")
    root = ET.fromstring(raw)
    namespace = "http://www.w3.org/2000/svg"
    if root.tag not in ("svg", "{" + namespace + "}svg"):
        raise ValueError("无效 SVG 文档")
    allowed = {"svg", "g", "defs", "style", "title", "desc", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan", "linearGradient", "radialGradient", "stop", "clipPath", "mask", "pattern", "use", "symbol", "marker", "filter", "feGaussianBlur", "feOffset", "feBlend", "feColorMatrix", "feComposite", "feMerge", "feMergeNode", "feFlood", "feDropShadow"}
    nodes = list(root.iter())
    if len(nodes) > 50000:
        raise ValueError("SVG 元素数量过多")
    for element in nodes:
        if element.tag.rsplit('}', 1)[-1] == 'style':
            rules = []
            for selector, declarations in re.findall(r'([^{}]+)\{([^{}]*)\}', element.text or ''):
                if re.fullmatch(r'[\w.#, >+~:\[\]="\-\s]+', selector):
                    rules.append(selector + '{' + safe_svg_style(declarations) + '}')
            element.text = '\n'.join(rules)
        for child in list(element):
            if child.tag.rsplit("}", 1)[-1] not in allowed:
                element.remove(child)
        for key, value in list(element.attrib.items()):
            name = key.rsplit("}", 1)[-1].lower()
            compact = re.sub(r"\s+", "", value).lower()
            if name == 'style':
                element.attrib[key] = safe_svg_style(value)
                continue
            unsafe = name.startswith("on") or name in {"base", "src"}
            unsafe = unsafe or (name == "href" and not re.fullmatch(r"#[A-Za-z_][\w.:-]*", value))
            unsafe = unsafe or any(token in compact for token in ("javascript:", "data:", "http:", "https:", "file:", "\\", "@import"))
            unsafe = unsafe or ("url(" in compact and not re.fullmatch(r"url\(#[A-Za-z_][\w.:-]*\)", compact))
            if unsafe:
                del element.attrib[key]
    ET.register_namespace("", namespace)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def safe_svg_style(text):
    properties = {'fill', 'fill-rule', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-opacity', 'stroke-dasharray', 'opacity', 'color', 'font-family', 'font-size', 'font-weight', 'font-style', 'text-anchor', 'dominant-baseline', 'letter-spacing', 'stop-color', 'stop-opacity', 'display', 'visibility', 'clip-path', 'filter'}
    safe = []
    for declaration in text.split(';'):
        key, separator, value = declaration.partition(':')
        if not separator or key.strip().lower() not in properties:
            continue
        if not re.fullmatch(r'[\w\s#.,%+()\-"\']*', value):
            continue
        if '(' in value and not re.fullmatch(r'\s*(?:rgba?\([\d\s.,%]+\)|url\(#[\w.:-]+\))\s*', value):
            continue
        safe.append(key.strip() + ':' + value.strip())
    return ';'.join(safe)


def svg_dimensions(raw):
    root = ET.fromstring(clean_svg(raw))
    viewbox = re.split(r"[\s,]+", root.get("viewBox", "").strip())
    def length(value):
        match = re.fullmatch(r"(\d+(?:\.\d+)?)(?:px)?", value or "")
        return float(match[1]) if match else 0
    width, height = length(root.get("width")), length(root.get("height"))
    if not (width and height) and len(viewbox) == 4:
        width, height = float(viewbox[2]), float(viewbox[3])
    if not (math.isfinite(width) and math.isfinite(height) and 0 < width <= 8192 and 0 < height <= 8192 and width * height <= 32_000_000):
        raise ValueError("SVG 缺少有效尺寸或尺寸过大")
    return round(width), round(height)


def raster_svg(raw):
    from svg_renderer import resvg_py
    safe = clean_svg(raw)
    width, height = svg_dimensions(safe)
    scale = min(1, 2048 / max(width, height))
    return resvg_py.svg_to_bytes(svg_string=safe.decode("utf-8"), width=max(1, round(width * scale)), height=max(1, round(height * scale)))


def image_info(raw, content_type="", max_bytes=MAX_OUTPUT_BYTES):
    """Trust decoded content, not a URL suffix or an upstream MIME claim."""
    if max_bytes and len(raw) > max_bytes:
        raise ValueError("图片文件超过 40 MB")
    if "svg" in content_type.lower() or raw.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        width, height = svg_dimensions(raw)
        return ".svg", "image/svg+xml", width, height, False
    with Image.open(BytesIO(raw)) as image:
        extension, mime = {"PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg"), "WEBP": (".webp", "image/webp"), "GIF": (".gif", "image/gif"), "BMP": (".bmp", "image/bmp"), "AVIF": (".avif", "image/avif")}[image.format]
        width, height = image.size
        animated = getattr(image, "n_frames", 1) > 1
        image.verify()
    return extension, mime, width, height, animated
