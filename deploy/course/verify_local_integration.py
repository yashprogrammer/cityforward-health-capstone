import base64,hashlib,secrets,subprocess
from urllib.parse import parse_qs,urlparse
import httpx
base='http://localhost:18095'
password=secrets.token_urlsafe(24)
p=subprocess.run(['docker','exec','-i','cityforward-integrated-staging-meditrack-1','python','-c',"import sys; from meditrack.backend.local_login import set_password; set_password('attending.raman', sys.stdin.read())"],input=password,text=True,capture_output=True)
if p.returncode: raise RuntimeError('Could not provision temporary staging account: '+p.stderr[-600:])
c=httpx.Client(base_url=base,follow_redirects=False,trust_env=False,timeout=30)
def ok(r,expected=(200,)):
 if r.status_code not in expected: raise RuntimeError(f'{r.request.method} {r.request.url.path}: HTTP {r.status_code}: {r.text[:250]}')
 return r
ok(c.post('/auth/login',json={'username':'attending.raman','password':password}))
chart=ok(c.get('/int/ip/IP2610-0142')).json()
link=next(x for x in chart['links'] if x['DISP_MD']=='SMART')
launch_response=ok(c.get(link['HREF']), (302,))
launch=parse_qs(urlparse(launch_response.headers['location']).query)['launch'][0]
verifier=secrets.token_urlsafe(48)
challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
redirect=base+'/ai/auth/callback'
params={'response_type':'code','client_id':'cityforward-ai-dashboard','redirect_uri':redirect,'scope':'launch openid fhirUser patient/*.read user/Practitioner.read patient/DocumentReference.write','state':secrets.token_urlsafe(12),'aud':base+'/fhir','launch':launch,'code_challenge':challenge,'code_challenge_method':'S256'}
auth=ok(c.get('/smart/authorize',params=params),(302,))
code=parse_qs(urlparse(auth.headers['location']).query)['code'][0]
token=ok(c.post('/smart/token',data={'grant_type':'authorization_code','code':code,'redirect_uri':redirect,'client_id':'cityforward-ai-dashboard','code_verifier':verifier})).json()
patient=token['patient'];headers={'Authorization':'Bearer '+token['access_token']}
r=ok(c.get(f'/ai/api/patients/{patient}/dashboard',headers=headers))
print('Staff login, patient chart, SMART launch and integrated dashboard: passed',flush=True)
s=ok(c.post(f'/ai/api/patients/{patient}/summary',headers=headers))
print('Integrated summary endpoint: passed (offline test provider)',flush=True)
csrf=c.cookies.get('mt_csrf');ok(c.post('/auth/logout',headers={'X-CSRF-Token':csrf}))
r=c.get(f'/ai/api/patients/{patient}/dashboard',headers=headers)
if r.status_code!=401: raise RuntimeError(f'Revoked session should be rejected, got {r.status_code}')
print('Revoked staff session blocks the AI dashboard: passed',flush=True)
