"""Private single-host qualification using unchanged FrontierCS-2 images.

This drives the native verifier directly. It is not Management, Driver, a
frontier-submit API implementation, or a scored Agent experiment.
"""
import argparse
import base64
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / 'source-manifest.json').read_text())
TASK = MANIFEST['task']
ACTIVE = set()
NNP_BOOTSTRAP = '''import ctypes,json,os,pathlib,sys
libc=ctypes.CDLL(None,use_errno=True)
if libc.prctl(38,1,0,0,0)!=0:
 raise OSError(ctypes.get_errno(),"PR_SET_NO_NEW_PRIVS failed")
status=dict(line.split(":",1) for line in pathlib.Path("/proc/self/status").read_text().splitlines() if ":" in line)
proof={key:status[key].strip() for key in ["NoNewPrivs","CapEff","Seccomp"]}
proof["uid"]=os.getuid()
proof["apparmor"]=pathlib.Path("/proc/self/attr/current").read_text().strip()
assert proof["NoNewPrivs"]=="1" and int(proof["CapEff"],16)==0 and proof["Seccomp"]=="2"
assert "docker-default" in proof["apparmor"]
if os.environ.get("FRONTIER_LOCAL_SECURITY_RECEIPT"):
 pathlib.Path(os.environ["FRONTIER_LOCAL_SECURITY_RECEIPT"]).write_text(json.dumps(proof))
os.execv(sys.argv[1],sys.argv[1:])
'''


def digest(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False))
    temporary.replace(path)


def image(key):
    value = TASK[key]
    return value['repository'] + '@' + value['digest']


def cleanup():
    for name in list(ACTIVE):
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=20)
        ACTIVE.discard(name)


def interrupted(signum, frame):
    cleanup()
    raise SystemExit(128 + signum)


def base_container(name, key, uid, memory):
    # All timed implementations share one fixed logical CPU. The workspace
    # tmpfs is bounded independently; its pages also count toward memory.
    return ['docker', 'run', '--name', name, '--pull=never', '--network=none',
            '--read-only', '--cap-drop=ALL',
            '--cpus=1', '--cpuset-cpus=' + str(int(os.environ.get('FRONTIER_LOCAL_GRADER_CPU',max(os.sched_getaffinity(0))))),
            '--memory=' + memory, '--memory-swap=' + memory, '--pids-limit=512',
            '--user=' + str(uid) + ':' + str(uid)]


def prepare_workspace():
    reference = ROOT / 'operator-only/reference.patch'
    assert digest(reference.read_bytes()) == TASK['reference']['payload_sha256']
    name = 'fcs2-blc-workspace-20261102-r02'
    code = r'''
import subprocess,pathlib,json,hashlib,base64,sys
def run(args):
 r=subprocess.run(args,capture_output=True,text=True,timeout=120)
 return r
r=run(['/frontier/task/prepare_workspace.sh'])
assert r.returncode==0,r.stderr
empty=run(['/frontier/task/make_submission.sh'])
assert empty.returncode==2 and 'empty' in empty.stderr,empty.stderr
r=run(['git','-C','/workspace/repo','apply','/qualification/reference.patch'])
assert r.returncode==0,r.stderr
r=run(['/frontier/task/make_submission.sh'])
assert r.returncode==0,r.stderr
r=run(['tar','-C','/workspace','--format=ustar','--sort=name',
 '--mtime=UTC 1970-01-01','--owner=0','--group=0','-cf','/workspace/generated.tar','payload'])
assert r.returncode==0,r.stderr
assert not pathlib.Path('/opt/frontier/task/evaluator.py').exists()
assert not pathlib.Path('/frontier/task/judge').exists()
body=pathlib.Path('/workspace/generated.tar').read_bytes()
patch=pathlib.Path('/workspace/payload/solution.patch').read_bytes()
assert 0<len(body)<=524288
status=dict(l.split(':',1) for l in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in l)
assert status['NoNewPrivs'].strip()=='1' and int(status['CapEff'].strip(),16)==0
print(json.dumps({'empty_patch_rejected':True,'task_image_has_no_judge':True,
 'no_new_privs':status['NoNewPrivs'].strip(),'effective_capabilities':status['CapEff'].strip(),
 'patch_sha256':'sha256:'+hashlib.sha256(patch).hexdigest(),
 'archive_sha256':'sha256:'+hashlib.sha256(body).hexdigest(),
 'archive_base64':base64.b64encode(body).decode()}))
'''
    cmd = base_container(name, 'agent_image', 10001, '4g') + [
        '--tmpfs', '/workspace:rw,exec,nosuid,nodev,size=4294967296,uid=10001,gid=10001,mode=0700',
        '--mount', 'type=bind,src=' + str(reference) + ',dst=/qualification/reference.patch,readonly',
        '-i', '--entrypoint=/usr/bin/python3', image('agent_image'),
        '-c', NNP_BOOTSTRAP, '/usr/bin/python3', '-']
    ACTIVE.add(name)
    try:
        result = subprocess.run(cmd, input=code.encode(), capture_output=True, timeout=180)
        (ROOT / 'logs/workspace.stderr').write_bytes(result.stderr)
        assert result.returncode == 0, result.stderr.decode(errors='replace')[-2500:]
        receipt = json.loads(result.stdout)
        archive = base64.b64decode(receipt.pop('archive_base64'))
        (ROOT / 'inputs/generated.tar').write_bytes(archive)
        receipt['matches_frozen_reference_patch'] = receipt['patch_sha256'] == TASK['reference']['payload_sha256']
        write_json(ROOT / 'workspace-receipt.json', receipt)
        return receipt
    finally:
        cleanup()


def make_archive(path, patch, symlink=False):
    with tarfile.open(path, 'w', format=tarfile.USTAR_FORMAT) as archive:
        directory = tarfile.TarInfo('payload')
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        archive.addfile(directory)
        member = tarfile.TarInfo('payload/solution.patch')
        member.mode = 0o644
        if symlink:
            member.type = tarfile.SYMTYPE
            member.linkname = '/etc/passwd'
            archive.addfile(member)
        else:
            member.size = len(patch)
            archive.addfile(member, io.BytesIO(patch))
    assert path.stat().st_size <= 524288


def decode_evidence(path):
    code = ('import io,json,tarfile,zstandard; '
            'raw=zstandard.ZstdDecompressor().decompress(open("/evidence.tar.zst","rb").read()); '
            't=tarfile.open(fileobj=io.BytesIO(raw)); '
            'print(t.extractfile("metrics.json").read().decode())')
    result = subprocess.run([
        'docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
        '--cap-drop=ALL', '--memory=256m', '--cpus=1',
        '--mount', 'type=bind,src=' + str(path) + ',dst=/evidence.tar.zst,readonly',
        '--entrypoint=/usr/bin/python3', image('verifier_image'),
        '-c', NNP_BOOTSTRAP, '/opt/frontier/runtime-python', '-c', code],
        capture_output=True, timeout=30, check=True)
    return json.loads(result.stdout)


def verify(archive, mode, label):
    assert mode in ('train', 'eval')
    dataset = ROOT / ('inputs/' + mode + '.json')
    assert digest(dataset.read_bytes()) == TASK['datasets'][mode + '_sha256']
    out = ROOT / 'runs' / label
    out.mkdir(mode=0o700)
    os.chown(out, 65532, 65532)
    name = 'fcs2-blc-' + label + '-20261102-r02'
    cmd = base_container(name, 'verifier_image', 65532, '4g') + [
        '--tmpfs', '/frontier:rw,exec,nosuid,nodev,size=4294967296,uid=65532,gid=65532,mode=0700',
        '--mount', 'type=bind,src=' + str(archive.resolve()) + ',dst=/inputs/payload.tar,readonly',
        '--mount', 'type=bind,src=' + str(dataset) + ',dst=/inputs/dataset.json,readonly',
        '--mount', 'type=bind,src=' + str(out) + ',dst=/out',
        '-e', 'FRONTIER_PAYLOAD_PATH=/inputs/payload.tar',
        '-e', 'FRONTIER_DATASET_PATH=/inputs/dataset.json',
        '-e', 'FRONTIER_VERIFICATION_MODE=' + mode,
        '-e', 'FRONTIER_RESULT_PATH=/out/result.json',
        '-e', 'FRONTIER_EVIDENCE_PATH=/out/evidence.tar.zst',
        '-e', 'FRONTIER_LOCAL_SECURITY_RECEIPT=/out/launch-security.json',
        '--entrypoint=/usr/bin/python3', image('verifier_image'),
        '-c', NNP_BOOTSTRAP, '/opt/frontier/verifier']
    write_json(out / 'invocation.json', {'argv': cmd, 'payload_sha256': digest(archive.read_bytes()),
                                        'dataset_sha256': digest(dataset.read_bytes())})
    started = time.monotonic()
    ACTIVE.add(name)
    try:
        with (out / 'process.log').open('wb') as log:
            process = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        inspect = subprocess.run(['docker', 'inspect', name], capture_output=True, check=True, timeout=15)
        container = json.loads(inspect.stdout)[0]
        write_json(out / 'container.json', {k: container[k] for k in ['Id', 'State', 'HostConfig', 'Image']})
        result = json.loads((out / 'result.json').read_text())
        row = {'label': label, 'mode': mode, 'process_exit_code': process.returncode,
               'elapsed_seconds': round(time.monotonic() - started, 3),
               'result': result, 'oom_killed': container['State']['OOMKilled']}
        if (out / 'evidence.tar.zst').exists():
            evidence = decode_evidence(out / 'evidence.tar.zst')
            write_json(out / 'operator-metrics.json', evidence)
            row['evidence_sha256'] = digest((out / 'evidence.tar.zst').read_bytes())
            row['operator_metrics'] = evidence['metrics']
        write_json(out / 'receipt.json', row)
        return row
    finally:
        cleanup()


def qualify():
    completed = []
    def progress(stage):
        write_json(ROOT / 'qualification-state.json', {'stage': stage, 'completed': completed,
                   'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat()})
    try:
        assert json.loads((ROOT / 'pull-state.json').read_text())['stage'] == 'pulled'
        progress('preparing_workspace')
        workspace = prepare_workspace()
        reference = ROOT / 'operator-only/reference.patch'
        make_archive(ROOT / 'inputs/reference.tar', reference.read_bytes())
        make_archive(ROOT / 'inputs/empty.tar', b'')
        make_archive(ROOT / 'inputs/symlink.tar', b'', symlink=True)
        forbidden = (b'diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n'
                     b'@@ -1 +1 @@\n-# Batch Local Counting\n+# Forbidden qualification edit\n')
        make_archive(ROOT / 'inputs/forbidden.tar', forbidden)
        archive = ROOT / 'inputs/generated.tar'
        # Retain the authored byte identity for exact-reference deduplication.
        # If native diff formatting differs, verify both paths explicitly.
        for mode in ['train', 'eval']:
            label = 'reference-' + mode
            progress(label)
            row = verify(ROOT / 'inputs/reference.tar', mode, label)
            completed.append(row)
            metrics = row.get('operator_metrics', {})
            assert row['process_exit_code'] == 0 and row['result']['status'] == 'passed', row
            assert metrics.get('valid_patch') == 1, row
            assert len(metrics.get('beat_spec_workload_passes', [])) == 6, row
            assert all(r['metrics']['exact_counts'] for r in metrics['beat_spec_workload_passes']), row
            assert metrics['beats_reference'] == 0, 'Exact reference must not beat itself'
            assert metrics['margin'] == 0, 'Deduplicated reference scores must match'
            if mode == 'train':
                assert 'beat_spec_progress' in row['result']['metrics'], row
                assert 'private_metrics' not in row['result'], row
            else:
                assert 'beat_spec_progress' not in row['result']['metrics'], row
        if not workspace['matches_frozen_reference_patch']:
            progress('generated-train')
            row = verify(archive, 'train', 'generated-train')
            completed.append(row)
            assert row['process_exit_code'] == 0 and row.get('operator_metrics', {}).get('valid_patch') == 1, row
            assert all(r['metrics']['exact_counts'] for r in row['operator_metrics']['beat_spec_workload_passes']), row
        for label in ['empty', 'symlink', 'forbidden']:
            progress('negative-' + label)
            row = verify(ROOT / ('inputs/' + label + '.tar'), 'train', 'negative-' + label)
            completed.append(row)
            if label in ['empty', 'symlink']:
                assert row['process_exit_code'] != 0 and row['result']['status'] == 'failed', row
                assert row['result']['failure']['code'] == 'invalid_submission', row
            else:
                assert row['process_exit_code'] == 0 and row['result']['status'] == 'passed', row
                assert row['result']['raw_score'] == 0 and row['operator_metrics'].get('valid_patch') != 1, row
                assert all(not x['metrics']['exact_counts'] for x in row['operator_metrics']['beat_spec_workload_passes']), row
        progress('qualified')
    except BaseException as error:
        write_json(ROOT / 'qualification-state.json', {'stage': 'failed', 'completed': completed,
                   'error': type(error).__name__ + ': ' + str(error)[:3500]})
        raise


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('qualify')
    grading = sub.add_parser('verify')
    grading.add_argument('--archive', type=Path, required=True)
    grading.add_argument('--mode', choices=['train', 'eval'], required=True)
    grading.add_argument('--label', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'qualify':
            qualify()
        else:
            if not args.label.replace('-', '').isalnum():
                raise SystemExit('label must contain only letters, numbers and hyphens')
            print(json.dumps(verify(args.archive, args.mode, args.label), indent=2))
    finally:
        cleanup()
