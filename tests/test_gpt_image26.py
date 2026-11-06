import asyncio
import base64
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import httpx
from fastapi import HTTPException
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend/py-libs'))
sys.path.insert(0, str(ROOT / 'backend/static/agent-runtime'))
import gpt_image26 as g
import test_desktop_backend as backend_fixture

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240"><rect width="320" height="240" fill="red"/></svg>'


class ContractTests(unittest.TestCase):
    def test_options_and_current_restrictions(self):
        for quality in g.QUALITIES:
            result = g.normalize('3:4', '1K', 'Sunburst', quality, 'jpg', 'high')
            self.assertEqual((result['quality'], result['version'], result['output_format']), (quality, 'sunburst', 'jpeg'))
        for kwargs in ({'resolution':'2k'}, {'reference_count':17}, {'output_format':'gif','size':'3:4'}, {'output_format':'svg','reference_count':1}, {'quality':'ultra'}, {'svg':{'model':'test'}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(HTTPException):
                g.normalize(**kwargs)
        self.assertNotIn('quality', g.normalize())
        self.assertNotIn('background', g.normalize())
        self.assertEqual(g.normalize(svg={'model':'test','reasoning':{'effort':'max'}},output_format='svg')['svg']['reasoning']['effort'], 'max')

    def test_mixed_dimensions_match_per_input(self):
        self.assertEqual([g.closest_ratio(w,h) for w,h in [(1024,768),(1000,1000),(941,1672),(398,320)]], ['4:3','1:1','9:16','5:4'])
        self.assertEqual(g.normalize(size='1086x1448')['size'], '3:4')

    def test_svg_is_rasterized_without_active_content(self):
        unsafe=SVG.replace(b'<rect', b'<script>alert(1)</script><image href="file:///secret"/><foreignObject>bad</foreignObject><rect')
        safe=g.clean_svg(unsafe)
        self.assertNotIn(b'script',safe)
        self.assertNotIn(b'file:',safe)
        self.assertNotIn(b'foreignObject',safe)
        png=g.raster_svg(unsafe)
        self.assertTrue(png.startswith(b'\x89PNG'))
        self.assertEqual(g.image_info(unsafe,'image/svg+xml')[2:4],(320,240))
        with self.assertRaises(ValueError):
            g.clean_svg(b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///secret">]><svg/>')

    def test_gif_bytes_preserve_animation_and_reference_format(self):
        buf=BytesIO()
        Image.new('RGB',(16,16),'red').save(buf,format='GIF',save_all=True,append_images=[Image.new('RGB',(16,16),'blue')],duration=80,loop=0)
        raw=buf.getvalue()
        self.assertEqual(g.image_info(raw)[0], '.gif')
        self.assertTrue(g.image_info(raw)[-1])
        url='data:image/gif;base64,'+base64.b64encode(raw).decode()
        self.assertEqual(g.reference_data_url({'url':url}, lambda _:None),url)

    def test_task_journal_survives_restart(self):
        with tempfile.TemporaryDirectory() as root:
            g.TaskJournal(root).update('task_a',provider_id='apimart',status='generating')
            self.assertEqual(g.TaskJournal(root).get('task_a')['status'],'generating')
            with self.assertRaises(HTTPException):
                g.TaskJournal(root).get('../../outside')


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        backend_fixture.BackendTests.setUpClass()
        cls.m=backend_fixture.BackendTests.module

    @classmethod
    def tearDownClass(cls):
        backend_fixture.BackendTests.tearDownClass()

    def test_real_adapter_batch_formats_and_recovery(self):
        m=self.m
        m.APIMART_IMAGE_INITIAL_POLL_DELAY=0
        buffer=BytesIO();Image.new('RGB',(30,40),'navy').save(buffer,format='PNG')
        png=buffer.getvalue()
        gif=BytesIO();Image.new('RGB',(30,40),'red').save(gif,format='GIF',save_all=True,append_images=[Image.new('RGB',(30,40),'blue')],duration=80,loop=0)
        posts=[]
        provider={'id':'apimart','name':'APIMART','protocol':'apimart','base_url':'https://fixture.invalid','image_request_mode':'openai'}
        original_client=httpx.AsyncClient
        def transport(request):
            if request.method=='POST':
                body=json.loads(request.content);posts.append(body)
                return httpx.Response(200,json={'data':[{'task_id':'task_'+str(len(posts)),'status':'submitted'}]})
            if request.url.path.startswith('/v1/tasks/'):
                task=request.url.path.rsplit('/',1)[1]
                fmt=posts[int(task.split('_')[1])-1]['output_format']
                return httpx.Response(200,json={'data':{'id':task,'status':'completed','result':{'images':[{'url':['https://fixture.invalid/output.'+fmt]}]}}})
            fmt=request.url.path.rsplit('.',1)[-1]
            raw={'svg':SVG,'gif':gif.getvalue()}.get(fmt,png)
            return httpx.Response(200,content=raw,headers={'content-type':{'svg':'image/svg+xml','gif':'image/gif'}.get(fmt,'image/png')})
        async def run():
            for fmt in ('png','gif','svg'):
                payload=m.OnlineImageRequest(provider_id='apimart',model=g.MODEL,prompt='fixture',size='auto' if fmt=='gif' else '3:4',resolution='1k',quality='max',output_format=fmt,version='sunburst',complexity='high',n=2)
                result=await m.build_online_image_result(payload)
                self.assertEqual(len(result['images']),2)
                for url in result['images']:
                    self.assertTrue(url.endswith('.'+fmt))
                    path=m.output_file_from_url(url)
                    if fmt=='svg':
                        self.assertEqual(Path(path).read_bytes(),SVG)
                        self.assertEqual(m.safe_media_response(path).media_type,'image/png')
                        with m.open_media_image(path) as image:self.assertEqual(image.size,(320,240))
                    if fmt=='gif':self.assertTrue(m.image_output_meta(url)['animated'])
                before=len(posts)
                recovered=await m.query_image_task(m.ImageTaskQueryRequest(task_id=result['task_id'],provider_id='apimart'))
                self.assertEqual(recovered['status'],'succeeded')
                self.assertEqual(len(posts),before)
            self.assertEqual(len(posts),6)
            self.assertTrue(all(p['n']==1 and p['version']=='sunburst' and p['quality']=='max' and p['complexity']=='high' for p in posts))
            self.assertTrue(all('background' not in p and 'official_fallback' not in p for p in posts))
        with patch.object(m,'get_api_provider',return_value=provider),patch.object(m,'api_headers',return_value={}),patch.object(m.httpx,'AsyncClient',side_effect=lambda **kwargs:original_client(transport=httpx.MockTransport(transport),**kwargs)):
            asyncio.run(run())

    def test_parameter_schema_and_reference_limit(self):
        fields=self.m.build_image_param_fields('api',{'id':'apimart','protocol':'apimart'},g.MODEL)
        by_key={field['key']:field for field in fields}
        self.assertEqual(self.m.apimart_image_reference_limit(g.MODEL),16)
        self.assertEqual(by_key['reference_images']['max'],16)
        self.assertEqual(by_key['size']['resolutions'],[{'value':'1k','label':'1K'}])
        self.assertNotIn('background',by_key)
        self.assertIn('complexity',by_key)

    def test_partial_failure_preserves_success_and_reference_sixteen(self):
        m=self.m
        m.APIMART_IMAGE_INITIAL_POLL_DELAY=0
        buf=BytesIO();Image.new('RGB',(32,32),'green').save(buf,format='PNG')
        raw=buf.getvalue()
        ref={'url':'data:image/png;base64,'+base64.b64encode(raw).decode(),'kind':'image'}
        posts=[]
        original_client=httpx.AsyncClient
        provider={'id':'apimart','protocol':'apimart','base_url':'https://fixture.invalid','image_request_mode':'openai'}
        def transport(request):
            if request.method=='POST':
                posts.append(json.loads(request.content))
                return httpx.Response(200,json={'data':[{'task_id':'task_partial_'+str(len(posts))}]})
            if '/v1/tasks/' in request.url.path:
                task=request.url.path.rsplit('/',1)[1]
                if task.endswith('_2'):
                    return httpx.Response(200,json={'data':{'status':'failed','error':{'message':'fixture failure'}}})
                return httpx.Response(200,json={'data':{'status':'completed','result':{'images':[{'url':['https://fixture.invalid/result.png']}]}}})
            return httpx.Response(200,content=raw,headers={'content-type':'image/png'})
        async def run():
            result=await m.build_online_image_result(m.OnlineImageRequest(provider_id='apimart',model=g.MODEL,prompt='fixture',size='4:3',n=4,reference_images=[ref]*16))
            self.assertEqual(len(result['images']),3)
            self.assertEqual(len(result['partial_errors']),1)
            self.assertEqual(result['partial_errors'][0]['task_id'],'task_partial_2')
            self.assertEqual(m.IMAGE26_JOURNAL.get('task_partial_2')['status'],'failed')
            self.assertTrue(all(len(body['image_urls'])==16 for body in posts))
            self.assertTrue(all(body['n']==1 for body in posts))
            for args in ({'resolution':'4k'},{'reference_images':[ref]*17},{'reference_images':[{'url':''}]}):
                with self.assertRaises(HTTPException):
                    await m.build_online_image_result(m.OnlineImageRequest(provider_id='apimart',model=g.MODEL,prompt='invalid',**args))
            self.assertEqual(len(posts),4)
        with patch.object(m,'get_api_provider',return_value=provider),patch.object(m,'api_headers',return_value={}),patch.object(m.httpx,'AsyncClient',side_effect=lambda **kwargs:original_client(transport=httpx.MockTransport(transport),**kwargs)):
            asyncio.run(run())

    def test_svg_http_preview_and_original_attachment(self):
        m=self.m
        path=Path(m.OUTPUT_OUTPUT_DIR)/'fixture.svg'
        path.write_bytes(SVG)
        url=m.output_url_for(path.name)
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app),base_url='http://fixture') as client:
                response=await client.get(url)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers['content-type'],'image/png')
                self.assertTrue(response.content.startswith(b'\x89PNG'))
                response=await client.get('/api/download-output',params={'url':url})
                self.assertEqual(response.content,SVG)
                self.assertIn('attachment',response.headers['content-disposition'])
        asyncio.run(run())

    def test_svg_project_zip_round_trip_retains_original_bytes(self):
        from fastapi import UploadFile
        m=self.m
        path=Path(m.OUTPUT_OUTPUT_DIR)/'roundtrip.svg'
        path.write_bytes(SVG)
        url=m.output_url_for(path.name)
        m.save_canvas({'id':'gpt26-roundtrip','title':'SVG roundtrip','kind':'smart','project':m.DEFAULT_PROJECT_ID,'nodes':[{'id':'svg','type':'smart-image','images':[{'url':url,'kind':'image','output_format':'svg'}]}],'connections':[]})
        async def run():
            response=await m.export_project(m.DEFAULT_PROJECT_ID)
            raw=Path(response.path).read_bytes()
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                manifest=json.loads(archive.read('resources-manifest.json'))
                entry=next(item for item in manifest['resources'] if item['url']==url)
                self.assertEqual(archive.read(entry['file']),SVG)
            imported=await m.import_project(UploadFile(filename='fixture.zip',file=BytesIO(raw)))
            self.assertTrue(imported)
            self.assertTrue(any(p.read_bytes()==SVG for p in Path(m.ASSETS_DIR).rglob('*.svg')))
            await response.background()
        asyncio.run(run())


if __name__=='__main__':
    unittest.main()
