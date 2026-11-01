import asyncio
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, AsyncMock

import httpx
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/static/agent-runtime'))
from minimax_video import MiniMaxVideoService, UpgradeRequest


class UpgradeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.posts = []
        self.gets = []
        self.account = 'test-only-account'
        self.mode = ''
        self.downloads = 0
        self.original_client = httpx.AsyncClient
        self.provider = dict(id='apimart', base_url='https://fixture.invalid')
        async def saved(url, probe_results):
            self.downloads += 1
            if self.mode == 'download-failure':
                raise OSError('offline')
            probe_results.append({'streams':[{'codec_type':'video','width':1440,'height':2560}]})
            return '/output/2k.mp4'
        self.m = types.SimpleNamespace(DATA_DIR=self.temp.name, CANVAS_TASKS={},
            get_api_provider=lambda _:self.provider, is_apimart_provider=lambda p:True,
            provider_env_key_value=lambda _:self.account, video_api_root=lambda p:'https://fixture.invalid',
            video_task_url_candidates=lambda p,b,t,u:[b+'/tasks/'+t],
            video_submit_url_candidates=lambda p,b:[b+'/videos/generations'],
            api_headers=lambda **kwargs:{}, extract_task_id=lambda raw:raw.get('task_id'),
            VIDEO_TASK_SUCCESS_STATUSES={'COMPLETED'}, VIDEO_TASK_FAILURE_STATUSES={'FAILED'},
            video_output_urls=lambda raw:raw.get('videos',[]), save_remote_video_to_output=saved,
            minimax_h3_video_metadata=lambda probe,aspect:{'width':1440,'height':2560,'warning':''})
        self.service = MiniMaxVideoService(self.m)
        self.source = dict(provider_id='apimart',task_id='source',model='MiniMax-H3',resolution='768P',aspect_ratio='9:16')
        self.service.remember(self.source)
        def transport(request):
            if request.method == 'POST':
                self.posts.append(json.loads(request.content))
                if self.mode == 'ambiguous':
                    raise httpx.ReadTimeout('accepted but response lost')
                if self.mode == 'rejected':
                    return httpx.Response(402,json={})
                return httpx.Response(200,json={'task_id':'upgrade'})
            self.gets.append(request.url.path)
            if request.url.path.endswith('/source'):
                if self.mode == 'source-max':
                    return httpx.Response(200,json={'status':'completed','model':'MiniMax-H3-Max','resolution':'768P'})
                return httpx.Response(200,json={'status':'completed','model':'MiniMax-H3','resolution':'768P'})
            return httpx.Response(200,json={'status':'completed','videos':['https://fixture.invalid/2k.mp4']})
        self.client_patch = patch('minimax_video.httpx.AsyncClient', lambda **kwargs:self.original_client(transport=httpx.MockTransport(transport),**kwargs))
        self.client_patch.start()

    async def asyncTearDown(self):
        await self.service.close()
        self.client_patch.stop()
        self.temp.cleanup()

    async def start(self, **kwargs):
        return await self.service.start(UpgradeRequest(provider_id='apimart',source_task_id='source',confirmed=True,**kwargs))

    async def settle(self, job):
        task=self.service.active.get(job['id'])
        if task:
            await task
            await asyncio.sleep(0)
        return self.service.read(job['id'])

    async def test_duplicate_clicks_submit_once_and_preserve_distinct_task_metadata(self):
        first,second=await asyncio.gather(self.start(),self.start())
        self.assertEqual(first['id'],second['id'])
        job=await self.settle(first)
        self.assertEqual(job['status'],'completed')
        self.assertEqual(self.posts,[{'model':'MiniMax-H3-Regeneration','source_task_id':'source'}])
        self.assertEqual(job['outputs'][0]['h3_request']['task_id'],'upgrade')
        self.assertEqual(job['outputs'][0]['h3_request']['source_task_id'],'source')
        self.assertEqual(self.downloads,1)
        await self.start()
        self.assertEqual(len(self.posts),1)
        self.assertNotIn('source_key',await self.service.status(job['id']))

    async def test_confirmation_and_max_source_rejected_before_submission(self):
        with self.assertRaises(HTTPException):
            await self.service.start(UpgradeRequest(provider_id='apimart',source_task_id='source'))
        self.service.remember({**self.source,'model':'MiniMax-H3-Max'})
        with self.assertRaises(HTTPException):
            await self.start()
        self.assertFalse(self.posts)

    async def test_legacy_metadata_is_checked_against_upstream(self):
        self.mode='source-max'
        job=await self.start()
        final=await self.settle(job)
        self.assertEqual(final['status'],'failed')
        self.assertFalse(self.posts)

    async def test_unknown_submission_never_auto_resubmits_even_after_restart(self):
        self.mode='ambiguous'
        job=await self.start()
        self.assertEqual((await self.settle(job))['status'],'uncertain')
        self.service=MiniMaxVideoService(self.m)
        await self.service.status(job['id'])
        await self.start(retry=True)
        self.assertEqual(len(self.posts),1)

    async def test_download_failure_resumes_existing_task_without_another_charge(self):
        self.mode='download-failure'
        job=await self.start()
        self.assertEqual((await self.settle(job))['status'],'waiting')
        self.service=MiniMaxVideoService(self.m)
        self.mode=''
        await self.service.status(job['id'])
        final=await self.settle(job)
        self.assertEqual(final['status'],'completed')
        self.assertEqual(len(self.posts),1)
        self.assertEqual(self.gets.count('/tasks/source'),1)

    async def test_restart_during_submission_without_id_stops_for_manual_check(self):
        job=await self.start()
        await self.settle(job)
        self.service.write(self.service.read(job['id']),status='submitting',upstream_task_id='')
        restarted=MiniMaxVideoService(self.m)
        await restarted.status(job['id'])
        self.assertEqual(restarted.read(job['id'])['status'],'uncertain')
        self.assertEqual(len(self.posts),1)

    async def test_definite_rejection_needs_explicit_retry_and_account_is_bound(self):
        self.mode='rejected'
        job=await self.start()
        self.assertEqual((await self.settle(job))['status'],'failed')
        await self.service.status(job['id'])
        self.assertEqual(len(self.posts),1)
        self.mode=''
        await self.start(retry=True)
        self.assertEqual((await self.settle(job))['status'],'completed')
        self.assertEqual(len(self.posts),2)
        self.account='other-test-account'
        with self.assertRaises(HTTPException):
            await self.service.status(job['id'])


class AdaptationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('minimax_backend_fixture',ROOT/'tests/test_desktop_backend.py')
        cls.fixture=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.fixture)
        cls.fixture.BackendTests.setUpClass()
        cls.m=cls.fixture.BackendTests.module

    @classmethod
    def tearDownClass(cls):
        cls.fixture.BackendTests.tearDownClass()

    def adapt(self, **kwargs):
        payload=self.m.CanvasVideoRequest(prompt='fixture',model=kwargs.pop('model','MiniMax-H3-Max'),**kwargs)
        roles=self.m.minimax_h3_input_roles(payload)
        return self.m.adapt_minimax_h3_body({'image_with_roles':[{'role':r} for r in roles]},payload)

    def test_max_defaults_ranges_and_watermark_1080p_omission(self):
        self.assertEqual(self.adapt()['resolution'],'768P')
        self.assertTrue(self.adapt(watermark=True)['watermark'])
        self.assertNotIn('watermark',self.adapt(resolution='1080p',watermark=True))
        for kwargs in ({'duration':4},{'resolution':'2k'},{'duration':16},{'enable_upsample':True}):
            with self.assertRaises(HTTPException):self.adapt(**kwargs)
        self.assertEqual(self.adapt(model='MiniMax-H3',duration=4)['resolution'],'2K')

    def test_frames_follow_input_and_reference_limits_do_not_truncate(self):
        image={'url':'https://fixture.invalid/a.png'}
        self.assertEqual(self.adapt(images=[image],use_frame_roles=True,aspect_ratio='9:16')['aspect_ratio'],'adaptive')
        for kwargs in ({'images':[image]*10},{'audios':['https://fixture.invalid/a.mp3']},{'images':[image],'videos':['v'], 'use_frame_roles':True}):
            with self.assertRaises(HTTPException):self.adapt(**kwargs)

    def test_max_real_route_accepts_mixed_references_and_returns_metadata_without_regeneration(self):
        m=self.m
        captured=[]
        async def uploaded(*args):return args[-1]
        async def post(client,url,**kwargs):
            captured.append(kwargs['json'])
            return httpx.Response(200,json={'task_id':'fixture-task'},request=httpx.Request('POST',url))
        async def save(url,**kwargs):
            kwargs['probe_results'].append({'streams':[{'codec_type':'video','width':720,'height':1280}]})
            return '/output/fixture.mp4'
        async def run():
            provider={'id':'apimart','name':'APIMART','protocol':'apimart','base_url':'https://fixture.invalid'}
            with contextlib.ExitStack() as stack:
                for name,value in [('get_api_provider',lambda _:provider),('provider_env_key_value',lambda _:'fixture-only'),('post_with_overload_retry',post),('upload_image_for_apimart',uploaded),('upload_video_for_apimart',uploaded),('prepare_apimart_video_reference',uploaded),('save_remote_video_to_output',save),('wait_for_video_task',AsyncMock(return_value={'videos':['https://fixture.invalid/out.mp4']}))]:stack.enter_context(patch.object(m,name,value))
                stack.enter_context(patch.object(m.httpx.AsyncClient,'send',side_effect=AssertionError('network disabled')))
                with contextlib.redirect_stdout(io.StringIO()):
                    return await m.canvas_video(m.CanvasVideoRequest(prompt='fixture',provider_id='apimart',model='MiniMax-H3-Max',images=[{'url':'https://fixture.invalid/a.png','role':'reference_image'}],videos=['https://fixture.invalid/a.mp4'],watermark=True))
        result=asyncio.run(run())
        self.assertEqual(len(captured),1)
        self.assertEqual(captured[0]['resolution'],'768P')
        self.assertTrue(captured[0]['watermark'])
        self.assertEqual(result['request_summary']['model'],'MiniMax-H3-Max')
        self.assertEqual(result['video_metadata'][0]['width'],720)


if __name__=='__main__': unittest.main()
