"""Publish the prepared transition document and diagram to the requested page."""
import base64,json,sys,urllib.request,uuid
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import auth
D=Path(__file__).resolve().parent
PAGE='24656478452'
TITLE='12-1.1.1-15.3 HMG–ADE Exalate Cloud 전환 계획 및 기존 연동 유지 대상'
assert auth.DOMAIN=='cloud-osci.atlassian.net'
BASE=f'https://{auth.DOMAIN}/wiki'
HEAD={'Authorization':'Basic '+base64.b64encode(f'{auth.EMAIL}:{auth.API_TOKEN}'.encode()).decode()}
def req(method,url,payload=None,headers=None):
 h=dict(HEAD);h.update(headers or {})
 if isinstance(payload,dict):payload=json.dumps(payload).encode();h['Content-Type']='application/json'
 with urllib.request.urlopen(urllib.request.Request(url,data=payload,headers=h,method=method),timeout=60) as r:return json.load(r)
url=BASE+'/api/v2/pages/'+PAGE
old=req('GET',url+'?body-format=storage')
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
(D/f'page_{PAGE}_{stamp}.backup.json').write_text(json.dumps(old,ensure_ascii=False,indent=2))
filename='exalate_transition.png';boundary='----'+uuid.uuid4().hex
payload=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: image/png\r\n\r\n').encode()+(D/filename).read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
a=req('PUT',BASE+f'/rest/api/content/{PAGE}/child/attachment',payload,{'Content-Type':'multipart/form-data; boundary='+boundary,'X-Atlassian-Token':'nocheck'})
body=(D/'project_classification.storage.html').read_text()
req('PUT',url,{'id':PAGE,'status':'current','title':TITLE,'body':{'representation':'storage','value':body},'version':{'number':old['version']['number']+1,'message':'실제 Trigger와 projects.txt 대조 및 Cloud 전환 구조도 반영'}})
saved=req('GET',url+'?body-format=storage');v=saved['body']['storage']['value']
assert saved['title']==TITLE
assert all(x in v for x in ['exalate_transition.png','CCRCTRACK','POIPTRACK','PARKINGLBM','51개','12개'])
assert v.count('<table')==2
attachments=req('GET',BASE+f'/api/v2/pages/{PAGE}/attachments')
assert any(x['title']==filename for x in attachments['results'])
print(json.dumps({'title':saved['title'],'version':saved['version']['number'],'verified_tables':2,'diagram':filename},ensure_ascii=False))
