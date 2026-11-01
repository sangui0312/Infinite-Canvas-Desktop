"""Isolated UI fixture: synthetic video and mocked upstream, no paid calls."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
original_connect=socket.socket.connect
def loopback_connect(sock,address):
    if isinstance(address,tuple) and address[0] not in ('127.0.0.1','localhost','::1'):
        raise RuntimeError('Fixture forbids external network')
    return original_connect(sock,address)
socket.socket.connect=loopback_connect

with tempfile.TemporaryDirectory(prefix='minimax-ui-',dir=ROOT/'.test-data') as data:
    os.environ.update(IC_DESKTOP_DATA_DIR=data,PYTHONDONTWRITEBYTECODE='1',API_PROVIDER_APIMART_KEY='fixture-only-not-real')
    sys.dont_write_bytecode=True
    (Path(data)/'data').mkdir()
    (Path(data)/'data/api_providers.json').write_text(json.dumps([dict(id='apimart',name='APIMART',base_url='https://fixture.invalid',protocol='apimart',enabled=True,video_models=['MiniMax-H3','MiniMax-H3-Max'],chat_models=[],image_models=[])]),encoding='utf-8')
    sys.path.insert(0,str(Path(os.environ.get('MINIMAX_UI_BACKEND',str(ROOT/'backend')))))
    import main as m
    import httpx
    import uvicorn
    import minimax_video
    clip=Path(m.OUTPUT_DIR)/'fixture.mp4'
    subprocess.run([m._project_tool_path('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=navy:s=180x320:r=24','-t','0.5','-c:v','libx264','-pix_fmt','yuv420p','-y',str(clip)],check=True,capture_output=True)
    for kind in ['classic','smart']:
        source=dict(provider_id='apimart',model='MiniMax-H3',resolution='768P',task_id='source-'+kind,aspect_ratio='9:16')
        item=dict(url='/output/fixture.mp4',name='768P preview.mp4',kind='video',width=180,height=320,h3_request=source)
        node=dict(id='preview',type='output' if kind=='classic' else 'smart-image',x=120,y=130,images=[item])
        m.save_canvas(dict(id='minimax-'+kind,title='MiniMax '+kind,kind=kind,nodes=[node],connections=[],settings={},viewport=dict(x=0,y=0,scale=1)))
        m.MINIMAX_VIDEO_SERVICE.remember(source)
    posts=[]
    polls={}
    original_client=httpx.AsyncClient
    def transport(request):
        if request.method=='POST':
            posts.append(json.loads(request.content))
            return httpx.Response(200,json=dict(data=[dict(task_id='upgrade-'+str(len(posts)),status='submitted')]))
        task=request.url.path.split('/')[-1]
        if task.startswith('source-'):
            return httpx.Response(200,json=dict(data=dict(status='completed',model='MiniMax-H3',resolution='768P')))
        polls[task]=polls.get(task,0)+1
        return httpx.Response(200,json=dict(data=dict(status='processing'))) if polls[task]==1 else httpx.Response(200,json=dict(data=dict(status='completed',result=dict(videos=[dict(url='https://fixture.invalid/'+task+'.mp4')]))))
    minimax_video.httpx.AsyncClient=lambda **kwargs:original_client(transport=httpx.MockTransport(transport),**kwargs)
    async def save(url,probe_results):
        name=url.split('/')[-1]
        shutil.copy2(clip,Path(m.OUTPUT_DIR)/name)
        probe_results.append(dict(streams=[dict(codec_type='video',width=1440,height=2560)]))
        return '/output/'+name
    m.save_remote_video_to_output=save
    @m.app.get('/__minimax_fixture')
    async def status():
        return dict(posts=posts)
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    print('MINIMAX_UI_READY '+str(sock.getsockname()[1]),flush=True)
    uvicorn.Server(uvicorn.Config(m.app,log_level='error')).run(sockets=[sock])
