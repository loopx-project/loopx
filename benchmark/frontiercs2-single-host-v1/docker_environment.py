"""Private Docker transport for the existing shared benchmark installer."""
import io
from pathlib import Path
import shlex
import subprocess
import tarfile
from types import SimpleNamespace


class DockerEnvironment:
    default_user = '10001:10001'

    def __init__(self, name, security_bootstrap, *, sealed=False):
        self.name = name
        self.security_bootstrap = security_bootstrap
        self.sealed = sealed

    async def exec(self, command, *, user=None, env=None, cwd=None, timeout_sec=None):
        effective_user = self.default_user if self.sealed else user or self.default_user
        argv = ['docker', 'exec', '--user', effective_user]
        if cwd:
            argv += ['--workdir', cwd]
        for key, value in (env or {}).items():
            argv += ['--env', key + '=' + str(value)]
        # Trusted installation runs as root. Every solver-side process enters
        # the verified no-new-privileges bootstrap before executing its argv.
        if not self.sealed and user in ('root', '0', '0:0', 0):
            argv += [self.name, '/bin/bash', '-c', command]
        else:
            argv += [self.name, '/usr/bin/python3', '-c', self.security_bootstrap,
                     '/bin/bash', '-c', command]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_sec or 300)
        if result.returncode:
            print('environment command exit', result.returncode, 'user', user,
                  'command', command[:200], 'stderr', result.stderr[-1200:], flush=True)
        return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)

    async def upload_file(self, source, target):
        if self.sealed:
            code = 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.write_bytes(sys.stdin.buffer.read()); p.chmod(0o644)'
            result = subprocess.run(['docker', 'exec', '-i', '--user', self.default_user,
                 self.name, '/usr/bin/python3', '-c', self.security_bootstrap,
                 '/usr/bin/python3', '-c', code, target], input=Path(source).read_bytes(),
                 capture_output=True, timeout=120)
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors='replace')[-1500:])
            return
        # Snap Docker has its own /tmp. Stream archive bytes rather than
        # asking its client to open a host temporary pathname.
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            def owned(info):
                info.uid = info.gid = 10001
                info.uname = info.gname = ''
                return info
            archive.add(source, arcname=Path(target).name, filter=owned)
        result = subprocess.run(['docker', 'cp', '-', self.name + ':' + str(Path(target).parent)],
                                input=stream.getvalue(), capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace')[-1500:])

    async def upload_dir(self, source, target):
        result = await self.exec('mkdir -p ' + shlex.quote(target), user='root')
        if result.return_code:
            raise RuntimeError(result.stderr)
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            def owned(info):
                info.uid = info.gid = 10001
                info.uname = info.gname = ''
                return info
            archive.add(source, arcname='.', filter=owned)
        result = subprocess.run(['docker', 'cp', '-', self.name + ':' + target],
                                input=stream.getvalue(), capture_output=True, timeout=240)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace')[-1500:])
