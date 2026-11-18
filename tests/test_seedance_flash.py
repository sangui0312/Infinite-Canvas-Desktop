import asyncio
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]


class FlashRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('flash_backend_fixture', ROOT / 'tests/test_desktop_backend.py')
        cls.fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.fixture)
        cls.fixture.BackendTests.setUpClass()
        cls.m = cls.fixture.BackendTests.module

    @classmethod
    def tearDownClass(cls):
        cls.fixture.BackendTests.tearDownClass()

    def run_request(self, result=None, **kwargs):
        m = self.m
        requests = []
        async def uploaded(*args):
            return args[-1]
        async def post(client, url, **options):
            requests.append(options['json'])
            return httpx.Response(200, json={'task_id':'fixture-task'}, request=httpx.Request('POST', url))
        async def save(url, **kwargs):
            return '/output/result.mp4'
        async def save_image(url):
            return '/output/last_frame.png'
        async def run():
            provider = {'id':'apimart','name':'APIMART','protocol':'apimart','base_url':'https://fixture.invalid'}
            with contextlib.ExitStack() as stack:
                for name, value in [('get_api_provider',lambda _:provider),('provider_env_key_value',lambda _:'fixture-only'),('post_with_overload_retry',post),('upload_image_for_apimart',uploaded),('upload_video_for_apimart',uploaded),('upload_audio_for_apimart',uploaded),('prepare_apimart_video_reference',AsyncMock(side_effect=AssertionError('Flash reference was trimmed'))),('save_remote_video_to_output',save),('save_remote_image_to_output',save_image),('wait_for_video_task',AsyncMock(return_value=result or {'videos':['https://fixture.invalid/result.mp4']}))]:
                    stack.enter_context(patch.object(m,name,value))
                stack.enter_context(patch.object(m.httpx.AsyncClient,'send',side_effect=AssertionError('network disabled')))
                with contextlib.redirect_stdout(io.StringIO()):
                    return await m.canvas_video(m.CanvasVideoRequest(prompt='fixture',provider_id='apimart',model='seedance-2-5-flash',**kwargs))
        response = asyncio.run(run())
        return requests, response

    def test_full_duration_mixed_media_and_explicit_audio(self):
        images = [{'url':f'https://fixture.invalid/{i}.png'} for i in range(10)]
        videos = [f'https://fixture.invalid/{i}.mp4' for i in range(5)]
        audios = [f'https://fixture.invalid/{i}.mp3' for i in range(5)]
        body = self.run_request(duration=30, output_format="mov", nsfw_check=True, web_search=True, seed=42, images=images, videos=videos, audios=audios, generate_audio=False)[0][0]
        self.assertEqual((body['output_format'], body['nsfw_check'], body['web_search'], body['seed']), ('mov', True, True, 42))
        self.assertEqual((body['duration'], body['resolution'], body['generate_audio']), (30, '480p', False))
        self.assertEqual((len(body['image_urls']),len(body['video_urls']),len(body['audio_urls'])), (10,5,5))

    def test_edit_frames_and_validation(self):
        body = self.run_request(duration=-1, omni_reference_task_type='edit', videos=['https://fixture.invalid/a.mp4'], images=[{'url':'https://fixture.invalid/a.png','role':'first_frame'}])[0][0]
        self.assertEqual((body['duration'],body['size'],body['omni_reference_task_type']), (-1,'adaptive','edit'))
        self.assertEqual(body['image_with_roles'][0]['role'], 'first_frame')
        for values in ({'duration':31},{'resolution':'1080p'},{'omni_reference_task_type':'edit'}, {'videos':['https://fixture.invalid/a.mp4'] * 6}):
            with self.subTest(values=values), self.assertRaises(HTTPException):
                self.run_request(**values)

    def test_nested_last_frame_is_not_downloaded_as_video(self):
        raw = {'data':{'result':{'videos':[{'url':['https://fixture.invalid/result.mp4'], 'last_frame_url':'https://fixture.invalid/frame.png'}]}}}
        _, response = self.run_request(result=raw, return_last_frame=True)
        self.assertEqual(response['videos'], ['/output/result.mp4'])
        self.assertEqual(response['images'][0]['url'], '/output/last_frame.png')


if __name__ == '__main__':
    unittest.main()
