"""Synthetic data and mocked APIMart requests for the real desktop canvas UI."""
import json
import os
from io import BytesIO
from pathlib import Path
import socket
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
original_connect=socket.socket.connect
def loopback_connect(sock,address):
    if isinstance(address,tuple) and address[0] not in ('127.0.0.1','localhost','::1'):
        raise RuntimeError('External network forbidden in fixture')
    return original_connect(sock,address)
socket.socket.connect=loopback_connect
with tempfile.TemporaryDirectory(prefix='gpt26-ui-',dir=ROOT/'.test-data') as data:
    os.environ.update(IC_DESKTOP_DATA_DIR=data,PYTHONDONTWRITEBYTECODE='1',API_PROVIDER_APIMART_KEY='fixture-only')
    sys.dont_write_bytecode=True
    (Path(data)/'data').mkdir()
    provider=dict(id='apimart',name='APIMART',base_url='https://fixture.invalid',protocol='apimart',image_request_mode='openai',enabled=True,image_models=['gpt-image-2.6-ext','gpt-image-2.5-ext'],chat_models=['fixture-chat'],video_models=[])
    (Path(data)/'data/api_providers.json').write_text(json.dumps([provider]),encoding='utf-8')
    sys.path.insert(0,os.environ.get('GPT26_UI_BACKEND',str(ROOT/'backend')))
    import main as m
    import httpx
    import uvicorn
    from PIL import Image
    m.APIMART_IMAGE_INITIAL_POLL_DELAY=0
    for name,size in [('landscape',(400,300)),('square',(300,300)),('portrait',(300,533))]:
        Image.new('RGB',size,'navy').save(Path(m.OUTPUT_OUTPUT_DIR)/(name+'.png'))
    gen=dict(id='gen',type='generator',x=110,y=120,model='gpt-image-2.6-ext',apiProvider='apimart',ratio='portrait43',resolution='1k',imageVersion='sunburst',outputFormat='png',count=1)
    prompt=dict(id='prompt',type='text',text='fixture poster',prompt='fixture poster',x=10,y=20)
    m.save_canvas(dict(id='gpt26-classic',title='Ext fixture',kind='classic',nodes=[prompt,gen],connections=[{'id':'link','from':'prompt','to':'gen','kind':'prompt'}],settings={},viewport=dict(x=0,y=0,scale=1)))
    m.save_canvas(dict(id='gpt26-smart',title='Ext fixture',kind='smart',nodes=[dict(id='image',type='smart-image',x=500,y=100,images=[dict(url=m.output_url_for('portrait.png'),name='portrait.png',width=300,height=533,kind='image')])],connections=[],settings=dict(engine='api',provider_id='apimart',model='gpt-image-2.6-ext',ratio='portrait43',resolution='1k',imageVersion='sunburst'),viewport=dict(x=0,y=0,scale=1)))
    gif=BytesIO();Image.new('RGB',(32,32),'red').save(gif,format='GIF',save_all=True,append_images=[Image.new('RGB',(32,32),'blue')],duration=80,loop=0)
    svg=b'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240"><rect width="320" height="240" fill="red"/></svg>'
    posts=[]
    original_client=httpx.AsyncClient
    image=BytesIO();Image.new('RGB',(30,40),'red').save(image,format='PNG')
    def transport(request):
        if request.method=='POST':
            body=json.loads(request.content);posts.append(body)
            return httpx.Response(200,json={'data':[{'task_id':'task_fixture_'+str(len(posts)),'status':'submitted'}]})
        if '/v1/tasks/' in request.url.path:
            task=request.url.path.rsplit('_',1)[-1]
            fmt=posts[int(task)-1]['output_format']
            return httpx.Response(200,json={'data':{'status':'completed','result':{'images':[{'url':['https://fixture.invalid/result.'+fmt]}]}}})
        fmt=request.url.path.rsplit('.',1)[-1]
        return httpx.Response(200,content={'gif':gif.getvalue(),'svg':svg}.get(fmt,image.getvalue()),headers={'content-type':{'gif':'image/gif','svg':'image/svg+xml'}.get(fmt,'image/png')})
    m.httpx.AsyncClient=lambda **kwargs:original_client(transport=httpx.MockTransport(transport),**kwargs)
    @m.app.get('/__gpt26_fixture')
    async def status():return {'posts':posts}
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    print('GPT26_UI_READY '+str(sock.getsockname()[1]),flush=True)
    uvicorn.Server(uvicorn.Config(m.app,log_level='error')).run(sockets=[sock])
