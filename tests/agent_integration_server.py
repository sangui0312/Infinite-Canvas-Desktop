"""Disposable loopback host for actual Agent UI integration; never uses real upstreams."""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "tests" / ".runtime-agent"
RUNTIME.mkdir(exist_ok=True)


def main():
    mixed = '--mixed' in sys.argv
    with tempfile.TemporaryDirectory(prefix="integration-", dir=RUNTIME) as data:
        os.environ["IC_DESKTOP_DATA_DIR"] = data
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        sys.dont_write_bytecode = True
        user = Path(data)
        (user / "data").mkdir()
        (user / "data" / "api_providers.json").write_text(json.dumps([{
            "id": "apimart", "name": "Integration fixture", "base_url": "http://127.0.0.1:1/v1",
            "protocol": "apimart", "enabled": True, "primary": True, "api_key": "",
            "chat_models": ["testllm"], "image_models": ["gpt-image-2"], "video_models": ['minimax-h3'] if mixed else []
        }]), encoding="utf-8")
        # A guard against accidental network activity from any real adapter.
        original_connect = socket.socket.connect
        def loopback_connect(sock, address):
            if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "localhost", "::1"):
                raise RuntimeError("Integration test forbids external network")
            return original_connect(sock, address)
        socket.socket.connect = loopback_connect
        sys.path.insert(0, str(ROOT / "backend"))
        import main as m
        from PIL import Image
        import uvicorn
        from fastapi import Request, HTTPException

        m.save_canvas({"id": "smoke", "title": "Agent integration fixture", "kind": "smart", "revision": 0,
                       "nodes": [{"id": "manual", "type": "smart-text", "title": "Manual draft", "prompt": "draft", "x": 80, "y": 100, "width": 280}],
                       "connections": [], "settings": {"engine": "api", "apiKind": "image", "provider_id": "apimart", "model": "gpt-image-2", "ratio": "square", "resolution": "1K"}, "viewport": {"x": 0, "y": 0, "scale": 1}})
        calls = {"llm": 0, "generation": 0, "active": 0, "peak": 0, "personas": []}
        async def fake_llm(messages, provider_id, model, tools=None):
            assert provider_id == "apimart" and model == "testllm"
            calls["llm"] += 1
            from canvas_agent.personas import PERSONAS
            role = next(key for key, role in PERSONAS.items() if role['prompt'] in messages[0]['content'])
            calls['personas'].append(role)
            if '--questions' in sys.argv and calls['llm'] <= 2:
                questions = [
                    {'id':'dimensions','question':'尺寸说明图缺少真实尺寸，如何处理？','options':['补充真实尺寸','取消尺寸图，替换为使用场景图']},
                    {'id':'usage','question':'商品主要用于什么场景？','options':[]}]
                if calls['llm'] == 2:
                    assert '取消尺寸图，替换为使用场景图' in messages[-1]['content']
                    questions = questions[1:]
                return {'text':json.dumps({'status':'needs_input','summary':'需要确认以下信息，已确定的内容会保留。','questions':questions,'steps':[]})}
            if '--questions' in sys.argv and calls['llm'] == 3:
                assert '商用烘焙工作台' in messages[-1]['content']
            steps = [{"id": "poster", "title": "测试海报", "kind": "image", "prompt": "fixture blue poster", "provider_id": "apimart", "model": "gpt-image-2", "references": [], "settings": {}, "count": 3}]
            if mixed:
                steps = [{'id':f'img-{i}', 'title':f'套图 {i+1}', 'kind':'image', 'provider_id':'apimart', 'model':'gpt-image-2', 'prompt':f'fixture scene {i}', 'references':[], 'settings':{'ratio':'1:1' if i<8 else '3:4','resolution':'4k' if i<8 else '2k'}} for i in range(20)]
                steps.append({'id':'film','title':'15秒视频','kind':'video','provider_id':'apimart','model':'minimax-h3','prompt':'fixture video','references':[], 'settings':{'ratio':'9:16','resolution':'768p','duration':15}})
            return {"text": json.dumps({"summary": "独立集成测试：制作素材", "steps": steps})}
        async def fake_image(payload):
            calls["generation"] += 1
            index = calls['generation']
            calls['active'] += 1
            calls['peak'] = max(calls['peak'], calls['active'])
            assert payload.provider_id == "apimart" and payload.model == "gpt-image-2"
            if mixed:
                assert payload.aspect_ratio in ('1:1','3:4') and payload.resolution in ('2k','4k')
            else:
                assert payload.aspect_ratio == "9:16" and payload.resolution == "2k"
            await asyncio.sleep(.25 if '--progress' in sys.argv else .06 if mixed else 1)
            calls['active'] -= 1
            name = f'integration-result-{index}.png'
            target = Path(m.OUTPUT_OUTPUT_DIR) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (256, 256), (40, 100, 210)).save(target)
            return {"image_items": [{"url": "/assets/output/" + name, "width": 256, "height": 256}]}
        m.CANVAS_AGENT_SERVICE.host["llm"] = fake_llm
        m.build_online_image_result = fake_image
        async def fake_video(payload):
            assert payload.model == 'minimax-h3' and payload.aspect_ratio == '9:16' and payload.duration == 15
            calls['generation'] += 1
            calls['video'] = calls.get('video',0) + 1
            name = 'integration-film.mp4'
            (Path(m.OUTPUT_OUTPUT_DIR) / name).write_bytes(b'fixture-video-not-for-playback')
            return {'videos':['/assets/output/'+name]}
        if mixed:
            m.canvas_video = fake_video
        config = uvicorn.Config(m.app, host="127.0.0.1", port=0, log_level="error", ws_ping_interval=None)
        server = uvicorn.Server(config)
        @m.app.get("/__agent_smoke/status")
        async def status():
            return {"calls": calls, "canvas": m.load_canvas("smoke")}
        @m.app.post("/__agent_smoke/shutdown")
        async def shutdown(request: Request):
            if request.client.host != "127.0.0.1":
                raise HTTPException(403)
            server.should_exit = True
            return {"stopping": True}
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            print("AGENT_SMOKE_READY " + str(listener.getsockname()[1]), flush=True)
            server.run(sockets=[listener])


if __name__ == "__main__":
    main()
