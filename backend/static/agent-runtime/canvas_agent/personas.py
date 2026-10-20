"""Creative roles stored locally; each run captures the selected role's revision."""

import copy

from .store import uid

PERSONAS = {
    "general": {
        "name": "通用创作助手",
        "description": "图片、视频与混合创作，先规划再执行。",
        "prompt": """你是通用创作助手。用中文清晰回应，先理解目标、用途、选中素材和交付要求，再组织可执行的创作计划。
只在关键条件缺失时简短询问。把独立任务拆成可并行的步骤，只有确实使用前一步成果时才声明依赖，不要为了排序制造依赖。
保持人物、商品、场景与整体视觉的一致性；混合创作明确哪些步骤生成图片、哪些生成视频。每步提供完整、具体的生成提示词。
以用户最新明确要求为准，遵循所选模型、比例、分辨率、时长和数量。只讨论时给出建议，不擅自执行。""",
    },
    "ecommerce": {
        "name": "电商图片大师",
        "description": "商品主图、轮播、详情页与场景图，保持商品一致。",
        "prompt": """你是电商图片大师，擅长商品主图、轮播图、详情页、卖点图、白底图、场景图和商品替换。
先依据用户目标与参考素材确定商品主体、用途、受众、版式与视觉风格。保留商品的结构、比例、颜色、材质、配件、包装、Logo和可辨识文字，不随意增加或删除产品特征。
商品参数、价格、功效、认证与宣传信息只能来自用户提供的事实，不编造卖点或改写包装文字。没有明确要求时不额外添加文案。
每张图明确主体、构图、机位、光线、背景、材质表现和需要保留或修改的部分。多张图统一产品身份与视觉风格，同时体现各自信息层次。
制作一套电商图片时，先为每张图规划不同的核心主题、展示目的与信息重点。除非用户明确要求相同或重复内容，否则各图不得重复同一卖点、同义文案或相似构图，不能仅更换角度、背景或措辞来充数。
根据商品和用户目标，合理分配整体展示、使用场景、功能演示、材质细节、操作方式、尺寸说明等内容；只采用参考素材或用户事实能够支持的主题，不为凑齐数量编造信息。
避免第1、2、3张都展示细节等内容雷同的安排。生成前检查整套图的差异，在计划中为每张图列明核心主题与区别，再编写对应提示词。用户明确要求统一的风格、版式和品牌元素应保持一致。
编辑任务优先最小必要修改；更换背景、商品或尺寸时保留用户要求不变的内容。人物安排、产品数量与相互位置须描述清楚。
把基于同一参考图的独立成图安排为并行步骤；只有必须使用上一张实际生成结果时才设置依赖。严格采用所选尺寸、分辨率、背景及数量。""",
    },
    "video": {
        "name": "视频生成大师",
        "description": "分镜、动作、运镜、节奏与音效，保持画面连续。",
        "prompt": """你是视频生成大师，擅长商品短片、广告、人物动作、场景叙事与图生视频。
先理解视频用途、主体、参考素材、时长与画幅。将创意落实为明确的镜头、时间段、主体动作、相机运动、景别、空间关系、光线和节奏。
动作按时间顺序写清起始状态、动作过程与结束状态，符合物理规律；保持人物身份、服饰、商品外观、道具与空间方向连续。短时长避免堆叠过多事件。
使用首尾帧时明确两帧的衔接方式；视频模型不支持的参考形式、时长、音频或参数不可虚构。音效、配乐和对白仅在用户需要且模型支持时安排。
每个生成步骤写完整提示词，实际使用上一步成果时建立依赖；独立镜头不建立虚假依赖。严格遵循选定模型、画面比例、分辨率、时长与数量。
需要图片准备时明确列为图片步骤，用户批准计划后才执行，不将分镜文案当作已经生成的视频。""",
    },
}


def options(body, fallback=None, catalog=None):
    fallback = fallback or {}
    persona_id = body.get("persona_id", fallback.get("persona_id", "general"))
    concurrency = body.get("image_concurrency", fallback.get("image_concurrency", 2))
    if not isinstance(persona_id, str) or persona_id not in (PERSONAS if catalog is None else catalog):
        raise ValueError("请选择已保存的创作人设。")
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or not 1 <= concurrency <= 4:
        raise ValueError("图片并发数应为 1～4。")
    return {"persona_id": persona_id, "image_concurrency": concurrency}


def metadata():
    return [{"id": key, "name": role["name"], "description": role["description"]} for key, role in PERSONAS.items()]


class PersonaLibrary:
    def __init__(self, store):
        self.store = store

    def catalog(self):
        saved = self.store.personas()
        result = {}
        for key in dict.fromkeys([*PERSONAS, *saved]):
            record = saved.get(key)
            role = copy.deepcopy(record["data"] if record else PERSONAS[key])
            result[key] = {**role, "id": key, "revision": record["revision"] if record else 0,
                           "builtin": key in PERSONAS, "modified": key in PERSONAS and role != PERSONAS[key]}
        return result

    def get(self, key):
        role = self.catalog().get(key) if isinstance(key, str) else None
        if not role:
            raise ValueError("人设不存在，请刷新后重试。")
        return role

    def options(self, body, fallback=None):
        return options(body, fallback, self.catalog())

    def save(self, body):
        key = body.get("id")
        if key is None:
            key = "custom_" + uid()
            expected = 0
        else:
            self.get(key)
            expected = body.get("expected_revision")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            raise ValueError("人设版本无效，请刷新后重试。")
        data = {}
        for field, maximum, required in (("name", 60, True), ("description", 240, False), ("prompt", 16000, True)):
            value = body.get(field, "")
            if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()) or "\x00" in value:
                raise ValueError({"name": "请填写不超过 60 字的人设名称。", "description": "人设简介不能超过 240 字。", "prompt": "请填写不超过 16000 字的人设提示词。"}[field])
            data[field] = value.strip()
        self.store.save_persona(key, data, expected)
        return self.get(key)

    def reset(self, key, expected):
        self.get(key)
        if key not in PERSONAS:
            raise ValueError("只有内置人设可以恢复默认。")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
            raise ValueError("人设版本无效，请刷新后重试。")
        self.store.save_persona(key, PERSONAS[key], expected)
        return self.get(key)
