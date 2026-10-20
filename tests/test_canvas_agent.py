"""Mock-only regression tests. Never load real settings, keys, canvases, or upstreams."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from fastapi import FastAPI, HTTPException
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "static" / "agent-runtime"))
from canvas_agent import AgentService, install_agent


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="canvas-agent-")
        self.canvas = {"id": "canvas", "revision": 0, "nodes": [
            {"id": "a", "type": "smart-image", "images": [{"url": "/assets/a.png", "width": 300, "height": 400}]},
            {"id": "b", "type": "smart-image", "images": [{"url": "/assets/b.png", "width": 200, "height": 300}]},
            {"id": "g", "type": "smart-group", "images": [{"url": "/assets/g1.png", "width": 400, "height": 400}, {"url": "/assets/g2.png", "width": 300, "height": 400}]},
        ]}
        self.generated, self.applied, self.queried, self.undone = [], {}, [], []
        self.reply = {"summary": "制作图片", "steps": [self.step()]}
        self.delivery_fail = False
        self.generation_gate = None
        self.result = None
        self.llm_calls = []

        async def llm(messages, provider_id, model, tools=None):
            self.llm_calls.append(copy.deepcopy(messages))
            if callable(self.reply):
                return self.reply(messages, tools)
            return {"text": json.dumps(self.reply)}

        async def generate(spec, on_submitted):
            self.generated.append(copy.deepcopy(spec))
            if self.generation_gate:
                await on_submitted("upstream-1", {"Authorization": "must-not-persist"})
                await self.generation_gate.wait()
            if callable(self.result):
                return self.result(spec)
            if isinstance(self.result, Exception):
                raise self.result
            return self.result or {"outputs": [{"url": "/outputs/" + spec["request_id"] + ".png", "kind": spec["kind"]}]}

        async def apply(canvas_id, operation_id, operation):
            if self.delivery_fail:
                raise HTTPException(409, "test-conflict")
            if operation_id not in self.applied:
                node_ids = ["out-" + operation_id + f"-{i}" for i, _ in enumerate(operation.get("outputs", [1]))]
                self.applied[operation_id] = {"canvas": {}, "operation_id": operation_id, "node_ids": node_ids}
                for node_id, output in zip(node_ids, operation.get("outputs", [])):
                    self.canvas["nodes"].append({"id": node_id, "images": [output]})
            return self.applied[operation_id]

        async def query(task):
            self.queried.append(task)
            return {"outputs": [{"url": "/outputs/recovered.png", "kind": "image"}]}

        async def undo(canvas_id, operation_id):
            self.undone.append(operation_id)
            return {"operation_id": operation_id, "conflicts": []}

        async def broadcast(canvas_id):
            pass

        self.host = {"data_dir": self.temp.name, "load_canvas": lambda _: copy.deepcopy(self.canvas), "providers": lambda: [{"id": "p", "name": "Mock", "chat_models": ["chat"], "image_models": ["image"], "video_models": ["video"]}], "llm": llm, "generate": generate, "query_task": query, "apply_operation": apply, "undo_operation": undo, "broadcast": broadcast, "capabilities": lambda *a: {"ratios": ["1:1", "3:4", "2:3", "9:16"], "resolutions": ["1K"], "durations": [4, 6], "max_references": 8}, "poll_interval": .01}
        self.service = AgentService(self.host)
        self.session = await self.service.create_session("canvas")

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    def step(self, **kwargs):
        return {"id": "one", "title": "海报", "kind": "image", "prompt": "product poster", "provider_id": "p", "model": "image", "references": ["a"], "settings": {"ratio": "3:4"}, **kwargs}

    def body(self, **kwargs):
        return {"client_request_id": "message-1", "text": "做海报", "context": {"canvas_revision": self.canvas["revision"], "selected_node_ids": ["a"], "scope": "selection"}, "llm": {"provider_id": "p", "model": "chat"}, "generation": {"provider_id": "p", "model": "image"}, **kwargs}

    async def settle(self, run_id, statuses=("awaiting_approval", "completed", "failed", "partial", "needs_input", "needs_attention", "stopped", "interrupted")):
        for _ in range(150):
            await asyncio.sleep(.005)
            run = self.service.require_run(run_id)
            if run["status"] in statuses:
                return self.service.snapshot(run)
        self.fail("Task did not settle")

    async def plan(self, body=None):
        run = await self.service.submit(self.session["id"], body or self.body())
        return await self.settle(run["id"])

    async def act(self, run, action, **kwargs):
        latest = self.service.require_run(run["id"])
        result = await self.service.action(run["id"], {"request_id": f"{action}-{latest['revision']}", "action": action, "expected_revision": latest["revision"], "plan_revision": (latest.get("plan") or {}).get("revision"), **kwargs})
        return result

    async def test_pending_parameters_revised_atomically_and_old_approval_rejected(self):
        self.host['capabilities'] = lambda *a: {'ratios': ['1:1', '9:16'], 'resolutions': ['2k', '4k'], 'durations': [4], 'max_references': 8}
        self.reply = {'summary': '原计划', 'steps': [self.step(settings={'ratio': '1:1', 'resolution': '4k'})]}
        run = await self.plan()
        body = {'request_id': 'edit', 'action': 'revise', 'expected_revision': run['revision'], 'plan_revision': 1, 'steps': [{'id': run['plan']['steps'][0]['id'], 'provider_id': 'p', 'model': 'image', 'settings': {'ratio': '9:16', 'resolution': '2k'}}]}
        revised = await self.service.action(run['id'], body)
        self.assertEqual(revised['plan']['revision'], 2)
        self.assertEqual(revised['plan']['steps'][0]['settings'], {'ratio': '9:16', 'resolution': '2k'})
        self.assertEqual(self.generated, [])
        await self.service.action(run['id'], body)  # Network retry does not revise twice.
        with self.assertRaises(HTTPException):
            await self.act(revised, 'approve', plan_revision=1)
        await self.act(revised, 'approve')
        await self.settle(run['id'])
        self.assertEqual(len(self.generated), 1)
        self.assertEqual(self.generated[0]['settings']['ratio'], '9:16')

    async def test_invalid_edit_preserves_pending_plan(self):
        run = await self.plan()
        with self.assertRaises(HTTPException):
            await self.act(run, 'revise', steps=[{'id': run['plan']['steps'][0]['id'], 'settings': {'ratio': '99:1'}}])
        self.assertEqual(self.service.snapshot(self.service.require_run(run['id'])), run)
        self.assertEqual(self.generated, [])

    async def test_pending_text_replans_without_generating(self):
        run = await self.plan()
        self.reply = {'summary': '改为竖屏', 'steps': [self.step(settings={'ratio': '9:16'})]}
        await self.act(run, 'revise', text='改为9:16')
        revised = await self.settle(run['id'])
        self.assertEqual(revised['status'], 'awaiting_approval')
        self.assertEqual(revised['plan']['revision'], 2)
        self.assertEqual(revised['plan']['steps'][0]['settings']['ratio'], '9:16')
        self.assertIn('改为9:16', self.llm_calls[-1][-1]['content'])
        self.assertEqual(self.generated, [])
        self.assertEqual((await self.service.submit(self.session['id'], self.body()))['id'], run['id'])

    async def test_video_duration_and_ratio_can_be_revised_before_submission(self):
        self.host['capabilities'] = lambda *a: {'ratios': ['16:9', '9:16'], 'resolutions': ['768p', '2k'], 'durations': [4, 15], 'max_references': 8}
        self.reply = {'summary': '视频', 'steps': [self.step(kind='video', model='video', settings={'ratio': '16:9', 'duration': 4})]}
        run = await self.plan(self.body(generation={'provider_id': 'p', 'model': 'video'}))
        revised = await self.act(run, 'revise', steps=[{'id': run['plan']['steps'][0]['id'], 'settings': {'ratio': '9:16', 'resolution': '2k', 'duration': 15}}])
        self.assertEqual(revised['steps'][0]['settings']['duration'], 15)
        self.assertEqual(self.generated, [])

    async def test_approval_message_action_idempotency_and_undo(self):
        run = await self.plan()
        same = await self.service.submit(self.session["id"], self.body())
        self.assertEqual(same["id"], run["id"])
        self.assertEqual(self.generated, [])
        body = {"request_id": "approval", "action": "approve", "expected_revision": run["revision"], "plan_revision": 1}
        await self.service.action(run["id"], body)
        await self.service.action(run["id"], body)
        final = await self.settle(run["id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(len(self.generated), 1)
        self.assertEqual(len(self.applied), 1)
        result = await self.service.undo(run["id"], {"request_id": "undo"})
        self.assertTrue(result["run"]["steps"][0]["undone"])
        await self.service.undo(run["id"], {"request_id": "undo"})
        self.assertEqual(len(self.undone), 1)
        self.assertTrue(any(m["role"] == "assistant" for m in final["messages"]))

    async def test_discussion_and_native_tools(self):
        self.reply = {"summary": "可以使用蓝色背景，先确定主标题。", "steps": []}
        run = await self.plan(self.body(text='先给方案，不生成'))
        self.assertEqual(run["status"], "completed")
        self.assertFalse(self.generated)
        def response(messages, tools):
            name, args = ("model_get_capabilities", {"provider_id": "p", "model": "image", "kind": "image"}) if len(self.llm_calls) == 2 else ("submit_plan", {"summary": "工具计划", "steps": [self.step()]})
            return {"choices": [{"message": {"tool_calls": [{"id": "t1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}
        self.reply = response
        run = await self.plan(self.body(client_request_id="message-2"))
        self.assertEqual(run["status"], "awaiting_approval")
        self.assertEqual(self.llm_calls[-1][-1]["role"], "tool")

    async def test_reject_unauthorized_reference_and_bad_capability(self):
        self.reply["steps"][0]["references"] = ["b"]
        run = await self.plan()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(len(self.llm_calls), 2)
        self.assertFalse(self.generated)
        self.reply["steps"][0]["references"] = ["a"]
        self.host["capabilities"] = lambda *args: {"max_references": 8}
        run = await self.plan(self.body(client_request_id="message-2"))
        self.assertEqual(run["status"], "needs_input")
        self.assertEqual(run['steps'][0]['status'], 'blocked')
        self.assertFalse(self.generated)

    async def test_21_distinct_mixed_steps_use_own_models_and_parameters(self):
        self.host['capabilities'] = lambda *a: {'ratios':['1:1','3:4','9:16'], 'resolutions':['2k','4k'], 'durations':[4,15], 'max_references':8}
        images = [self.step(id=f'carousel-{i}', settings={'ratio':'1:1','resolution':'4k'}, prompt=f'carousel scene {i}') for i in range(8)]
        images += [self.step(id=f'detail-{i}', settings={'ratio':'3:4','resolution':'2k'}, prompt=f'detail scene {i}') for i in range(12)]
        self.reply = {'summary':'21份素材', 'steps':images+[self.step(id='film',kind='video',model='video',settings={'ratio':'9:16','resolution':'2k','duration':15})]}
        defaults = {'image':{'apiKind':'image','provider_id':'p','model':'image','ratio':'1:1','resolution':'4k','count':1},'video':{'apiKind':'video','provider_id':'p','model':'video','ratio':'9:16','resolution':'2k','duration':15}}
        run = await self.plan(self.body(text='8张轮播图，12张详情图，1条15秒视频', generation_defaults=defaults))
        self.assertEqual(len(run['steps']),21)
        self.assertEqual([s['settings']['ratio'] for s in run['steps']], ['1:1']*8+['3:4']*12+['9:16'])
        self.assertEqual(run['steps'][-1]['model'],'video')
        self.assertIn('capability_catalog',self.llm_calls[-1][0]['content'])
        self.assertIn('durations',self.llm_calls[-1][0]['content'])
        await self.act(run,'approve')
        final = await self.settle(run['id'],('completed',))
        self.assertEqual(len(self.generated),21)
        self.assertEqual(len(final['outputs']),21)

    async def test_step_counts_not_overwritten_and_budget_is_sum(self):
        self.reply = {'summary':'组合数量', 'steps':[self.step(id='main', count=8),self.step(id='detail',count=12),self.step(id='film',kind='video',model='video',count=1,settings={'ratio':'9:16','duration':4})]}
        run = await self.plan(self.body(text='8张图片，12张图片，1条视频', generation={'count':1,'provider_id':'p','model':'image'}))
        self.assertEqual(len(run['steps']),21)
        self.assertEqual(run['steps'][-1]['model'],'video')

    async def test_unsupported_ratio_blocks_only_that_step_and_can_finish_later(self):
        self.reply = {'summary':'不同尺寸', 'steps':[self.step(id='ready'),self.step(id='long',settings={'ratio':'1:2'})]}
        run = await self.plan()
        self.assertEqual(run['status'],'awaiting_approval')
        self.assertEqual(run['steps'][1]['status'],'blocked')
        await self.act(run,'approve')
        partial = await self.settle(run['id'],('partial',))
        self.assertEqual(len(self.generated),1)
        revised = await self.act(partial,'revise',steps=[{'id':'step-2','settings':{'ratio':'3:4'}}])
        self.assertEqual(revised['steps'][0]['status'],'completed')
        await self.act(revised,'approve')
        final = await self.settle(run['id'],('completed',))
        self.assertEqual(len(self.generated),2)
        self.assertEqual([s['attempt'] for s in final['steps']],[1,1])

    async def test_empty_generation_response_needs_input_and_can_replan(self):
        self.reply = {'summary':'请补充视频比例', 'steps':[]}
        run = await self.plan()
        self.assertEqual(run['status'],'needs_input')
        self.assertIn('revise',run['actions'])
        self.assertNotIn('approve',run['actions'])
        self.reply = {'summary':'补充完成','steps':[self.step()]}
        await self.act(run,'revise',text='改为3:4图片')
        fixed = await self.settle(run['id'])
        self.assertEqual(fixed['status'],'awaiting_approval')
        self.assertEqual(self.generated,[])

    async def test_catalog_survives_text_only_tool_compatibility_fallback(self):
        def reply(messages, tools):
            self.assertIn('capability_catalog', messages[0]['content'])
            self.assertIn('video', messages[0]['content'])
            if tools is not None:
                raise HTTPException(400, 'tools unsupported')
            return {'text':json.dumps({'summary':'图与视频', 'steps':[self.step(),self.step(id='v',kind='video',model='video',settings={'ratio':'9:16','duration':4})]})}
        self.reply = reply
        run = await self.plan()
        self.assertEqual(run['status'],'awaiting_approval')
        self.assertEqual(len(run['steps']),2)
        self.assertEqual(len(self.llm_calls),2)
        self.assertEqual(self.generated,[])

    async def test_narrative_embedded_empty_json_is_never_generation_completed(self):
        self.reply = lambda *_: {'text':'不能形成完整计划。\n'+json.dumps({'summary':'缺少参数','steps':[]})}
        run = await self.plan()
        self.assertEqual(run['status'],'needs_input')
        self.assertNotIn('"steps"',run['plan']['summary'])
        self.assertEqual(self.generated,[])

    async def test_explicit_locks_remain_enforced(self):
        run = await self.plan(self.body(generation={'provider_id':'p','model':'image','ratio':'1:1','locks':['ratio']}))
        self.assertEqual(run['status'],'failed')
        self.assertEqual(self.generated,[])

    async def test_flat_mixed_dimensions_and_groups(self):
        self.reply["steps"] = [self.step(references=["a", "b"], loop="flat", settings={"size_mode": "source"})]
        run = await self.plan(self.body(context={"selected_node_ids": ["a", "b"]}))
        self.assertEqual([s["settings"]["ratio"] for s in run["plan"]["steps"]], ["3:4", "2:3"])
        await self.act(run, "approve")
        self.assertEqual((await self.settle(run["id"]))["status"], "completed")
        self.reply["steps"] = [self.step(references=["g"], loop="group", settings={"ratio": "1:1"})]
        run = await self.plan(self.body(client_request_id="message-2", context={"selected_node_ids": ["g"]}))
        self.assertEqual(len(run["steps"]), 1)
        self.assertEqual(len(run["steps"][0]["references"]), 2)

    async def test_stop_prevents_next_submission_and_keeps_accepted_result(self):
        self.reply["steps"][0]["count"] = 3
        self.generation_gate = asyncio.Event()
        run = await self.plan()
        await self.act(run, "approve")
        for _ in range(100):
            if self.generated:
                break
            await asyncio.sleep(.005)
        await self.service.action(run['id'], {'request_id':'stale-stop', 'action':'stop', 'expected_revision':0})
        self.generation_gate.set()
        final = await self.settle(run["id"], ("stopped",))
        self.assertEqual(len(self.generated), 2)
        self.assertEqual([s['status'] for s in final['steps']], ['completed', 'completed', 'pending'])
        self.assertNotIn("Authorization", json.dumps(final))

    async def test_concurrency_limits_and_fills_freed_slot(self):
        for limit in (1, 2, 4):
            gates = [asyncio.Event() for _ in range(4)]
            started, active, peak = [], set(), 0
            async def generate(spec, on_submitted):
                nonlocal peak
                index = int(spec['request_id'].split(':step-')[1].split(':')[0]) - 1
                started.append(index)
                active.add(index)
                peak = max(peak, len(active))
                await gates[index].wait()
                active.remove(index)
                return {'outputs': [{'url': f'/outputs/{limit}-{index}.png', 'kind': 'image'}]}
            self.host['generate'] = generate
            self.reply = {'summary': '并行', 'steps': [self.step(count=4)]}
            run = await self.plan(self.body(client_request_id=f'parallel-{limit}', image_concurrency=limit))
            await self.act(run, 'approve')
            for _ in range(100):
                if len(started) == limit:
                    break
                await asyncio.sleep(.005)
            self.assertEqual(len(started), limit)
            gates[0].set()
            if limit < 4:
                for _ in range(100):
                    if len(started) > limit:
                        break
                    await asyncio.sleep(.005)
                self.assertEqual(len(started), limit + 1)  # Does not wait for a whole batch.
            for gate in gates:
                gate.set()
            final = await self.settle(run['id'], ('completed',))
            self.assertEqual(peak, limit)
            self.assertEqual(len(final['outputs']), 4)

    async def test_group_loop_is_capped_at_two_even_when_four_selected(self):
        self.reply = {'summary': '按组', 'steps': [self.step(count=4, loop='group', references=['g'])]}
        self.generation_gate = asyncio.Event()
        run = await self.plan(self.body(context={'selected_node_ids': ['g']}, image_concurrency=4))
        await self.act(run, 'approve')
        for _ in range(100):
            if len(self.generated) >= 2:
                break
            await asyncio.sleep(.005)
        await asyncio.sleep(.02)
        self.assertEqual(len(self.generated), 2)
        self.generation_gate.set()
        await self.settle(run['id'], ('completed',))
        self.assertEqual(len(self.generated), 4)

    async def test_dependencies_wait_but_independent_steps_run_together(self):
        gates = {key: asyncio.Event() for key in ('step-1', 'step-2', 'step-3')}
        started = []
        async def generate(spec, on_submitted):
            key = spec['request_id'].split(':')[1]
            started.append(key)
            await gates[key].wait()
            return {'outputs': [{'url': '/outputs/' + key + '.png', 'kind': 'image'}]}
        self.host['generate'] = generate
        self.reply = {'summary': '依赖', 'steps': [self.step(), self.step(id='dependent', references=[{'step_id':'one'}], dependencies=['one']), self.step(id='independent')]}
        run = await self.plan()
        await self.act(run, 'approve')
        for _ in range(100):
            if len(started) == 2:
                break
            await asyncio.sleep(.005)
        self.assertEqual(started, ['step-1', 'step-3'])
        gates['step-1'].set()
        for _ in range(100):
            if 'step-2' in started:
                break
            await asyncio.sleep(.005)
        self.assertIn('step-2', started)
        for gate in gates.values():
            gate.set()
        await self.settle(run['id'], ('completed',))

    async def test_unknown_submission_halts_queue_but_keeps_other_inflight_result(self):
        gate = asyncio.Event()
        started = []
        async def generate(spec, on_submitted):
            started.append(spec['request_id'])
            if ':step-1:' in spec['request_id']:
                raise TimeoutError('ambiguous')
            await gate.wait()
            return {'outputs': [{'url':'/outputs/accepted.png', 'kind':'image'}]}
        self.host['generate'] = generate
        self.reply = {'summary':'未知提交', 'steps':[self.step(count=4)]}
        run = await self.plan()
        await self.act(run, 'approve')
        for _ in range(100):
            if len(started) == 2:
                break
            await asyncio.sleep(.005)
        gate.set()
        final = await self.settle(run['id'], ('needs_attention',))
        self.assertEqual(len(started), 2)
        self.assertEqual([s['status'] for s in final['steps']], ['unknown','completed','pending','pending'])

    async def test_video_remains_single_lane_while_images_overlap(self):
        active = {'image':0, 'video':0}
        peak = {'image':0, 'video':0}
        async def generate(spec, on_submitted):
            kind = spec['kind']
            active[kind] += 1
            peak[kind] = max(peak[kind], active[kind])
            await asyncio.sleep(.02)
            active[kind] -= 1
            return {'outputs':[{'url':'/outputs/mixed.' + ('mp4' if kind=='video' else 'png'), 'kind':kind}]}
        self.host['generate'] = generate
        self.reply = {'summary':'混合并发', 'steps':[self.step(count=2), self.step(id='video', kind='video', model='video', count=2)]}
        run = await self.plan(self.body(generation={'provider_id':'p'}, image_concurrency=4))
        await self.act(run, 'approve')
        await self.settle(run['id'], ('completed',))
        self.assertEqual(peak, {'image':2, 'video':1})

    async def test_personas_are_system_prompts_and_changes_require_new_approval(self):
        from canvas_agent.personas import PERSONAS
        for key, role in PERSONAS.items():
            run = await self.plan(self.body(client_request_id='role-' + key, persona_id=key))
            self.assertIn(role['prompt'], self.llm_calls[-1][0]['content'])
            self.assertEqual(run['plan']['persona_id'], key)
            self.assertEqual(run['plan']['image_concurrency'], 2)
        revised = await self.act(run, 'revise', persona_id='ecommerce', image_concurrency=3)
        revised = await self.settle(run['id'])
        self.assertEqual(revised['plan']['revision'], 2)
        self.assertEqual(revised['plan']['persona_id'], 'ecommerce')
        self.assertEqual(revised['plan']['image_concurrency'], 3)
        self.assertIn(PERSONAS['ecommerce']['prompt'], self.llm_calls[-1][0]['content'])
        self.assertEqual(self.generated, [])
        with self.assertRaises(HTTPException):
            await self.act(revised, 'approve', plan_revision=1)
        changed = await self.act(revised, 'revise', image_concurrency=1)
        self.assertEqual(changed['plan']['revision'], 3)
        self.assertEqual(changed['plan']['image_concurrency'], 1)
        for bad in ({'persona_id':'custom'}, {'image_concurrency':5}, {'image_concurrency':True}):
            with self.assertRaises(HTTPException):
                await self.service.submit(self.session['id'], self.body(**bad))

    async def test_structured_questions_answers_replan_without_generation_or_lost_requirements(self):
        self.reply = {'summary': '尺寸说明图缺少实际尺寸，其余已确定。', 'status': 'needs_input', 'questions': [
            {'id': 'dimensions', 'question': '尺寸说明图如何处理？', 'options': ['提供实际尺寸', '取消尺寸说明图，替换为使用场景图']},
            {'id': 'usage', 'question': '商品主要用于什么场景？', 'options': []}], 'steps': []}
        run = await self.plan()
        self.assertEqual(run['status'], 'needs_input')
        self.assertEqual(len(run['plan']['questions']), 2)
        self.assertEqual(run['actions'], ['revise'])
        self.assertEqual(self.generated, [])
        self.assertIn('待你回答', self.service.store.messages(self.session['id'])[-1]['content'])
        questions = copy.deepcopy(run['plan']['questions'])
        await self.service.close()
        self.service = AgentService(self.host)
        self.assertEqual(self.service.snapshot(self.service.require_run(run['id']))['plan']['questions'], questions)
        self.reply = {'summary': '按补充内容制作场景图。', 'steps': [self.step()]}
        revised = await self.act(run, 'revise', text='问题：尺寸说明图如何处理？\n回答：取消尺寸说明图，替换为使用场景图\n问题：商品主要用于什么场景？\n回答：商用烘焙工作台')
        revised = await self.settle(run['id'])
        self.assertEqual(revised['status'], 'awaiting_approval')
        self.assertEqual(revised['plan']['questions'], [])
        self.assertEqual(revised['plan']['revision'], 2)
        self.assertTrue(any(m['role'] == 'user' and '使用场景图' in m['content'] for m in self.llm_calls[-1]))
        self.assertEqual(self.generated, [])
        with self.assertRaises(HTTPException):
            await self.act(revised, 'approve', plan_revision=1)
        await self.act(revised, 'approve')
        await self.settle(run['id'], ('completed',))
        self.assertEqual(len(self.generated), 1)

    async def test_legacy_questions_extraction_and_no_guessing(self):
        from canvas_agent.planning import legacy_questions, validate_questions, PlanError
        text = '计划制作六张图。提交完整可执行计划前，还需补充两项事实：①商品实际尺寸及单位（按商品详情页提供长宽高、直径等，或提供清晰尺寸图）；②可直接查看的原始产品图及商品用途，便于准确安排使用场景。当前提供的是画布节点信息，无法代替事实。请补充上述信息。'
        extracted = legacy_questions(text)
        self.assertEqual(len(extracted), 2)
        self.assertTrue(extracted[0]['question'].startswith('商品实际尺寸'))
        self.assertTrue(extracted[1]['question'].startswith('可直接查看'))
        self.assertNotIn('当前提供', extracted[1]['question'])
        self.reply = {'summary': text, 'steps': [], 'status': 'needs_input'}
        run = await self.plan()
        self.assertEqual(run['plan']['questions'], extracted)
        current = self.service.require_run(run['id'])
        current['plan'].pop('questions')  # Compatibility with already saved tasks.
        self.assertEqual(self.service.snapshot(current)['plan']['questions'], extracted)
        fallback = legacy_questions('尚未形成完整计划。')
        self.assertEqual(fallback[0]['id'], 'clarify')
        self.assertIn('逐条列出', fallback[0]['options'][0])
        for bad in ([{'question': ''}], [{'id': 'q', 'question': 'x'}, {'id': 'q', 'question': 'y'}], [{'question': 'x', 'options': ['x' * 241]}], [{'question': 'x', 'execute': True}], ['x'] * 9):
            with self.assertRaises(PlanError):
                validate_questions(bad)
        self.assertEqual(validate_questions(['商品用途是什么？'])[0]['question'], '商品用途是什么？')

    async def test_questions_prevent_approval_even_if_model_also_proposes_steps(self):
        self.reply = {'summary': '需要确认真实尺寸。', 'status': 'ready', 'questions': [{'question': '提供尺寸，还是将尺寸图换为场景图？'}], 'steps': [self.step()]}
        run = await self.plan()
        self.assertEqual(run['status'], 'needs_input')
        self.assertNotIn('approve', run['actions'])
        self.assertEqual(self.generated, [])

    async def test_background_jobs_use_legacy_shell_task_registry(self):
        registry = {}
        self.service.host['active_task_registry'] = registry
        gate = asyncio.Event()
        self.service._spawn('registry-test', gate.wait())
        self.assertEqual(registry['agent:registry-test']['status'], 'running')
        gate.set()
        await self.service.tasks['registry-test']
        await asyncio.sleep(0)
        self.assertEqual(registry, {})

    async def test_persona_library_persists_overrides_custom_roles_and_reset(self):
        from canvas_agent.personas import PERSONAS
        library = self.service.personas
        original = library.get('ecommerce')
        changed = library.save({'id': 'ecommerce', 'expected_revision': 0, 'name': '我的电商导演', 'description': '套图主题各不相同', 'prompt': '每张图片使用不同主题，禁止重复卖点。'})
        self.assertTrue(changed['modified'])
        self.assertEqual(changed['revision'], 1)
        with self.assertRaisesRegex(ValueError, '其他窗口'):
            library.save({'id': 'ecommerce', 'expected_revision': 0, 'name': '过期编辑', 'prompt': '不能覆盖'})
        custom = library.save({'name': '服装摄影师', 'description': '服装展示', 'prompt': '保持服装身份，采用不同场景。'})
        self.assertFalse(custom['builtin'])
        run = await self.plan(self.body(persona_id=custom['id']))
        self.assertIn(custom['prompt'], self.llm_calls[-1][0]['content'])
        self.assertEqual(run['plan']['persona_name'], custom['name'])
        with self.assertRaises(ValueError):
            library.reset(custom['id'], custom['revision'])
        for invalid in ({'name': '', 'prompt': 'x'}, {'name': 'x', 'prompt': ''}, {'name': 'x' * 61, 'prompt': 'x'}, {'name': 'x', 'prompt': 'x' * 16001}):
            with self.assertRaises(ValueError):
                library.save(invalid)
        await self.service.close()
        self.service = AgentService(self.host)
        self.assertEqual(self.service.personas.get(custom['id']), custom)
        self.assertEqual(self.service.personas.get('ecommerce')['prompt'], changed['prompt'])
        restored = self.service.personas.reset('ecommerce', 1)
        self.assertEqual(restored['prompt'], PERSONAS['ecommerce']['prompt'])
        self.assertFalse(restored['modified'])
        self.assertEqual(restored['revision'], 2)
        self.assertIn('第1、2、3张都展示细节', original['prompt'])

    async def test_edit_same_persona_requires_replan_and_keeps_run_snapshot(self):
        run = await self.plan(self.body(persona_id='ecommerce'))
        original = self.service.require_run(run['id'])['persona_snapshot']['prompt']
        saved = self.service.personas.save({'id': 'ecommerce', 'expected_revision': 0, 'name': '新电商大师', 'prompt': '必须使用新的不重复主题规则。'})
        self.assertEqual(self.service.require_run(run['id'])['persona_snapshot']['prompt'], original)
        self.assertTrue(self.service.snapshot(self.service.require_run(run['id']))['persona_changed'])
        with self.assertRaisesRegex(HTTPException, '重新规划'):
            await self.act(run, 'approve')
        revised = await self.act(run, 'revise', persona_id='ecommerce')
        revised = await self.settle(run['id'])
        self.assertEqual(revised['plan']['revision'], 2)
        self.assertEqual(revised['persona_revision'], saved['revision'])
        self.assertFalse(revised['persona_changed'])
        self.assertIn(saved['prompt'], self.llm_calls[-1][0]['content'])
        with self.assertRaises(HTTPException):
            await self.act(revised, 'approve', plan_revision=1)
        self.generation_gate = asyncio.Event()
        await self.act(revised, 'approve')
        for _ in range(50):
            if self.generated:
                break
            await asyncio.sleep(.005)
        self.service.personas.save({'id': 'ecommerce', 'expected_revision': 1, 'name': '再次编辑', 'prompt': '仅用于下一个任务。'})
        self.assertEqual(self.service.require_run(run['id'])['persona_snapshot']['prompt'], saved['prompt'])
        self.generation_gate.set()
        final = await self.settle(run['id'], ('completed',))
        self.assertEqual(final['plan']['persona_name'], saved['name'])
        self.assertEqual(len(self.generated), 1)
        new_run = await self.plan(self.body(client_request_id='new-role-task', persona_id='ecommerce'))
        self.assertIn('仅用于下一个任务', self.llm_calls[-1][0]['content'])
        self.assertEqual(new_run['persona_revision'], 2)

    async def test_persona_http_management_validation_and_origin(self):
        app = FastAPI()
        backend = install_agent(app, self.host)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:31300') as client:
                response = await client.get('/api/canvas-agent/personas')
                self.assertEqual(len(response.json()['personas']), 3)
                self.assertTrue(all('prompt' in role for role in response.json()['personas']))
                data = {'name': '视觉导演', 'prompt': '不同任务分别构图。'}
                response = await client.post('/api/canvas-agent/personas', json=data, headers={'Origin': 'https://evil.test'})
                self.assertEqual(response.status_code, 403)
                response = await client.post('/api/canvas-agent/personas', json=data)
                custom = response.json()['persona']
                response = await client.post('/api/canvas-agent/personas', json={**data, 'id': custom['id'], 'expected_revision': 1, 'prompt': '修改后内容'})
                self.assertEqual(response.json()['persona']['revision'], 2)
                response = await client.post('/api/canvas-agent/personas', json={**data, 'id': custom['id'], 'expected_revision': 1})
                self.assertEqual(response.status_code, 422)
                response = await client.post('/api/canvas-agent/personas/general/reset', json={'expected_revision': 0})
                self.assertFalse(response.json()['persona']['modified'])
                response = await client.post('/api/canvas-agent/personas', json={'name': '空提示词', 'prompt': ''})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.generated, [])
        finally:
            await backend.close()

    async def test_partial_retry_and_delivery_repair_never_repay_successes(self):
        self.reply["steps"][0]["count"] = 3
        self.result = lambda spec: {"status": "failed"} if ":step-3:1" in spec["request_id"] else {"outputs": [{"url": "/outputs/ok.png", "kind": "image"}]}
        run = await self.plan()
        await self.act(run, "approve")
        final = await self.settle(run["id"], ("partial",))
        self.assertEqual([s["status"] for s in final["steps"]], ["completed", "completed", "failed"])
        self.delivery_fail = True
        await self.act(final, "retry", step_id="step-3")
        final = await self.settle(run["id"], ("needs_attention",))
        self.assertEqual(len(self.generated), 4)
        self.delivery_fail = False
        await self.act(final, "repair_delivery")
        final = await self.settle(run["id"], ("completed",))
        self.assertEqual(len(self.generated), 4)
        self.assertEqual(len(self.applied), 3)

    async def test_unknown_recovery_never_resubmits_and_known_queries(self):
        self.result = TimeoutError("secret-provider-token-must-not-appear")
        run = await self.plan()
        await self.act(run, "approve")
        final = await self.settle(run["id"], ("needs_attention",))
        self.assertEqual(final["steps"][0]["status"], "unknown")
        self.assertNotIn("secret-provider", json.dumps(final))
        await self.service.close()
        self.service = AgentService(self.host)
        await self.service.start()
        self.assertEqual(len(self.generated), 1)
        self.assertFalse(self.queried)
        with self.assertRaises(HTTPException):
            await self.act(final, "retry")
        current = self.service.require_run(run["id"])
        current["status"] = "running"
        current["steps"][0].update(status="waiting", upstream_id="recover-me")
        self.service.store.save(current)
        await self.service.close()
        self.service = AgentService(self.host)
        await self.service.start()
        final = await self.settle(run["id"], ("completed",))
        self.assertEqual(len(self.generated), 1)
        self.assertEqual(len(self.queried), 1)
        self.assertEqual(self.queried[0]["upstream_id"], "recover-me")

    async def test_canvas_single_execution_and_stale_approval(self):
        run = await self.plan()
        with self.assertRaises(HTTPException) as error:
            await self.service.action(run["id"], {"request_id": "bad-rev", "action": "approve", "expected_revision": 0, "plan_revision": 1})
        self.assertEqual(error.exception.status_code, 409)
        other = await self.plan(self.body(client_request_id="other"))
        self.generation_gate = asyncio.Event()
        await self.act(run, "approve")
        with self.assertRaises(HTTPException):
            await self.act(other, "approve")
        self.generation_gate.set()
        await self.settle(run["id"], ("completed",))

    async def test_http_origin_validation_and_event_replay(self):
        app = FastAPI()
        backend = install_agent(app, self.host)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:31300") as client:
            response = await client.get("/api/canvas-agent/providers", headers={"Origin": "https://evil.test"})
            self.assertEqual(response.status_code, 403)
            response = await client.post("/api/canvas-agent/sessions", json={"canvas_id": "../private"})
            self.assertEqual(response.status_code, 422)
            response = await client.post("/api/canvas-agent/sessions", json={"canvas_id": "canvas"}, headers={"Origin": "http://127.0.0.1:31300"})
            session_id = response.json()["session"]["id"]
            response = await client.post(f"/api/canvas-agent/sessions/{session_id}/messages", json=self.body())
            self.assertEqual(response.status_code, 202)
            run_id = response.json()["run_id"]
            for _ in range(30):
                await asyncio.sleep(.005)
                if backend.require_run(run_id)["status"] != "planning":
                    break
            events = backend.store.events(run_id, 0)
            self.assertGreaterEqual(len(events), 2)
            self.assertEqual(backend.store.events(run_id, events[-1]["seq"]), [])
            self.assertEqual(len({e["seq"] for e in events}), len(events))
        await backend.close()

    async def test_video_uses_actual_prior_output_and_operation_recovery(self):
        self.reply["steps"].append(self.step(id="video", kind="video", model="video", references=[{"step_id": "one"}], settings={"ratio": "9:16", "duration": 4}, dependencies=["one"]))
        run = await self.plan(self.body(generation={"provider_id": "p"}))
        self.assertEqual(run["status"], "awaiting_approval")
        await self.act(run, "approve")
        final = await self.settle(run["id"], ("completed", "partial", "failed"))
        self.assertEqual(final["status"], "completed")
        self.assertEqual(self.generated[1]["kind"], "video")
        self.assertEqual(self.generated[1]["settings"]["duration"], 4)
        self.assertEqual(self.generated[1]["references"][0]["url"], final["steps"][0]["outputs"][0]["url"])
        self.assertTrue(self.generated[1]["references"][0]["node_id"])
        # Simulate crash after the canvas receipt committed but before DB delivery state.
        current = self.service.require_run(run["id"])
        current["steps"][1].update(status="delivering", delivery_status="prepared")
        current["status"] = "running"
        self.service.store.save(current)
        await self.service.close()
        self.service = AgentService(self.host)
        await self.service.start()
        final = await self.settle(run["id"], ("completed",))
        self.assertEqual(len(self.generated), 2)
        self.assertEqual(len(self.applied), 2)

    async def test_restart_queries_without_resuming_pending_paid_work(self):
        self.reply["steps"][0]["count"] = 2
        run = await self.plan()
        current = self.service.require_run(run["id"])
        current.update(status="running", approved_revision=1)
        current["steps"][0].update(status="waiting", upstream_id="known", task_id="durable-id", attempt=1)
        self.service.store.save(current)
        await self.service.close()
        self.service = AgentService(self.host)
        await self.service.start()
        final = await self.settle(run["id"], ("interrupted",))
        self.assertFalse(self.generated)
        self.assertEqual(final["steps"][0]["status"], "completed")
        self.assertEqual(final["steps"][1]["status"], "pending")
        self.assertIn("continue", final["actions"])

    async def test_native_invalid_tool_corrects_once_and_keeps_tool_protocol(self):
        def reply(messages, tools):
            args = {"summary": "修正计划", "steps": [self.step()]}
            if len(self.llm_calls) == 1:
                args["steps"][0]["references"] = ["nonexistent"]
            return {"choices": [{"message": {"tool_calls": [{"id": "tool-" + str(len(self.llm_calls)), "type": "function", "function": {"name": "submit_plan", "arguments": json.dumps(args)}}]}}]}
        self.reply = reply
        run = await self.plan()
        self.assertEqual(run["status"], "awaiting_approval")
        transcript = self.llm_calls[1]
        self.assertTrue(any(m.get("tool_call_id") == "tool-1" for m in transcript))
        self.assertEqual(run["tool_calls"][0]["id"], "tool-2")

    async def test_reference_edit_before_execution_does_not_submit(self):
        run = await self.plan()
        self.canvas["nodes"][0]["images"][0]["url"] = "/assets/edited.png"
        await self.act(run, "approve")
        final = await self.settle(run["id"], ("failed",))
        self.assertFalse(self.generated)
        self.assertIn("变化", final["steps"][0]["error"])


if __name__ == "__main__":
    unittest.main()
