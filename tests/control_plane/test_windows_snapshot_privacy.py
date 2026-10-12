"""Native Windows private IO behavior; all state is disposable."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,tempfile
import pytest
from loopx.control_plane import effect_runtime as er
from loopx.control_plane.runtime import private_permissions as permissions
from loopx.control_plane.turn_driver import codex_sessions as sessions
from tests.control_plane.windows_private_acl import assert_private_acl

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows native DACL behavior')
R=Path.cwd()
NODE='''import {readPrivateJsonSnapshot,openPrivateResponseSink} from "MODULE";
const a=JSON.parse(process.argv[1]);let effects=0;
try{if(a.op==='read') await readPrivateJsonSnapshot(a.ref);
else {const h=await openPrivateResponseSink(a.ref.path);await h.close();}
effects++;process.stdout.write(JSON.stringify({accepted:true,effects}));}
catch(e){process.stdout.write(JSON.stringify({accepted:false,effects,error:e.message}));}
'''.replace('MODULE',(R/'loopx/control_plane/effect_runtime_snapshot.ts').as_uri())

def broaden(path):
    rights='*S-1-1-0:(OI)(CI)F' if path.is_dir() else '*S-1-1-0:F'
    r=subprocess.run(['icacls.exe',str(path),'/grant',rights],capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stdout+r.stderr

def typed(path,*,op='read',bad_digest=False):
    raw=path.read_bytes();ref={'path':str(path),'byte_count':len(raw),'sha256':'0'*64 if bad_digest else hashlib.sha256(raw).hexdigest()}
    r=subprocess.run([er._node_executable(),'--no-warnings','--experimental-strip-types','--input-type=module','-e',NODE,json.dumps({'op':op,'ref':ref})],env=dict(os.environ,LOOPX_EFFECT_RUNTIME_PYTHON_BIN=sys.executable),capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stdout+r.stderr
    return json.loads(r.stdout)

@pytest.fixture
def snapshot(tmp_path):
    parent=Path(tempfile.mkdtemp(prefix='loopx-effect-',dir=tmp_path))
    permissions.protect_private_path(parent,directory=True)
    path=parent/'response.json'
    payload={'schema_version':er.EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION,'request_id':'owned-request','ok':True,'result':{'blob':'x'*2200000}}
    path.write_text(json.dumps(payload,separators=(',',':')),encoding='utf8')
    permissions.protect_private_path(path)
    return path,payload

def envelope(path):
    raw=path.read_bytes()
    return {'schema_version':er.EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION,'request_id':'owned-request','ok':True,'result_ref':{'byte_count':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}}

def pyread(path,env=None):
    return er._read_local_snapshot_response(env or envelope(path),path,method='goal.checkpoint_read_context.commit',request_id='owned-request',timeout=10)

def test_actual_producer_protects_before_socket_dispatch(monkeypatch):
    seen=[]
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):return None
        def settimeout(self,value):pass
        def sendall(self,raw):
            request=json.loads(raw);sink=Path(request['response_sink']);params=Path(request['params_ref']['path'])
            for p in (sink.parent,sink,params):assert_private_acl(p)
            assert len(params.read_bytes())>er.MAX_REQUEST_BYTES
            seen.append(request)
        def recv(self,size):return json.dumps({'schema_version':er.EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION,'request_id':'producer','ok':True,'result':{}}).encode()+b'\n'
    monkeypatch.setattr(er.socket,'create_connection',lambda *a,**k:Connection())
    er._request_with_info({'token':'synthetic','host':'127.0.0.1','port':1},request_id='producer',method='goal.checkpoint_read_context.resolve',params={'large':'x'*2200000},timeout=2,large_local_snapshot=True)
    assert len(seen)==1 and not Path(seen[0]['response_sink']).exists()

def test_large_protected_bytes_and_digest(snapshot):
    path,payload=snapshot;assert_private_acl(path.parent);assert_private_acl(path)
    assert typed(path)=={'accepted':True,'effects':1}
    assert pyread(path)==payload
    assert typed(path,bad_digest=True)['accepted'] is False
    bad=envelope(path);bad['result_ref']['sha256']='0'*64
    with pytest.raises(er.EffectRuntimeResponseAmbiguous):pyread(path,bad)

@pytest.mark.parametrize('op',['read','sink'])
@pytest.mark.parametrize('target',['file','parent'])
def test_typed_rejects_broad_acl_before_effect(snapshot,op,target):
    path,_=snapshot;broaden(path if target=='file' else path.parent)
    result=typed(path,op=op)
    assert result['accepted'] is False and result['effects']==0

@pytest.mark.parametrize('target',['file','parent'])
def test_python_postdispatch_acl_failure_is_ambiguous(snapshot,target):
    path,_=snapshot;broaden(path if target=='file' else path.parent)
    with pytest.raises(er.EffectRuntimeResponseAmbiguous) as error:pyread(path)
    assert error.value.diagnostic_code=='runtime_response_ambiguous'

def test_session_writer_protects_and_keeps_original_identity(tmp_path):
    lineage={'goal_id':'isolated-privacy','agent_id':'fixture','todo_id':'original'}
    path=sessions._session_path(tmp_path,lineage,'agent');path.parent.mkdir(parents=True)
    permissions.protect_private_path(path.parent,directory=True);broaden(path.parent)
    sessions._store_codex_cli_session(tmp_path,lineage=lineage,session_scope='agent',session_id='original-session-id')
    assert_private_acl(path.parent);assert_private_acl(path)
    value=sessions.select_codex_cli_session(tmp_path,lineage=lineage,session_scope='agent')
    assert value['session_id']=='original-session-id' and value['agent_id']=='fixture'
    assert value['todo_id']=='original' and value['session_scope']=='agent'

@pytest.mark.parametrize('failure',['protect','replace'])
def test_session_publication_failure_keeps_prior_session(tmp_path,monkeypatch,failure):
    lineage={'goal_id':'isolated-privacy','agent_id':'fixture','todo_id':'original'}
    sessions._store_codex_cli_session(tmp_path,lineage=lineage,session_scope='agent',session_id='original-session-id')
    path=sessions._session_path(tmp_path,lineage,'agent');old=path.read_bytes()
    def deny(*a,**k):raise PermissionError('injected native IO refusal')
    if failure=='protect':
        if not hasattr(sessions,'protect_private_path'):pytest.fail('Original session writer has no protection admission')
        monkeypatch.setattr(sessions,'protect_private_path',deny)
    else:monkeypatch.setattr(sessions.os,'replace',deny)
    with pytest.raises(PermissionError):sessions._store_codex_cli_session(tmp_path,lineage=lineage,session_scope='agent',session_id='replacement')
    assert path.read_bytes()==old
    assert not [x for x in path.parent.iterdir() if x.name.startswith('.'+path.name+'.')]

def test_python_rejects_junction_parent(snapshot,tmp_path):
    path,_=snapshot;link=tmp_path/'loopx-effect-junction'
    cmd=['pwsh','-NoLogo','-NoProfile','-Command',"New-Item -ItemType Junction -Path $env:LX_LINK -Target $env:LX_TARGET | Out-Null"]
    r=subprocess.run(cmd,env=dict(os.environ,LX_LINK=str(link),LX_TARGET=str(path.parent)),capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stderr
    try:
        other=link/path.name
        assert typed(other)['accepted'] is False
        with pytest.raises(er.EffectRuntimeResponseAmbiguous):pyread(other)
    finally:os.rmdir(link)

@pytest.mark.parametrize('helper',['missing','invalid'])
def test_typed_missing_os_fact_helper_rejects(snapshot,helper):
    path,_=snapshot;raw=path.read_bytes();ref={'path':str(path),'byte_count':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    env=dict(os.environ);env.pop('LOOPX_EFFECT_RUNTIME_PYTHON_BIN',None)
    if helper=='invalid':env['LOOPX_EFFECT_RUNTIME_PYTHON_BIN']=str(path.parent/'absent-python.exe')
    r=subprocess.run([er._node_executable(),'--no-warnings','--experimental-strip-types','--input-type=module','-e',NODE,json.dumps({'op':'read','ref':ref})],env=env,capture_output=True,text=True,timeout=30)
    assert r.returncode==0,r.stderr
    result=json.loads(r.stdout);assert result['accepted'] is False and result['effects']==0
