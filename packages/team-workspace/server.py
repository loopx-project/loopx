#!/usr/bin/env python3
"""Loopback-only application server and resident scheduler."""
import argparse
import base64
import fcntl
import json
import mimetypes
import os
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

from model import Codex
from service import Workspace, text
from store import uid
from loopx_client import REPO


WEB = Path(__file__).parent / 'web'
MAX_BODY = 12_000_000


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, body, content_type='application/json; charset=utf-8'):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def trusted(self, write=False):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed:
                raise PermissionError('仅允许本机访问')
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + h for h in allowed}:
                raise PermissionError('不允许跨站请求')
            if write and (self.headers.get('X-Team-Workspace') != 'local' or
                          self.headers.get_content_type() != 'application/json'):
                raise PermissionError('写操作需要本地界面请求')

        def do_GET(self):
            try:
                self.trusted()
                route = urlparse(self.path)
                if route.path == '/api/state':
                    self.send(200, app.snapshot())
                elif route.path == '/healthz':
                    self.send(200, {'ok': True, 'application': 'team-workspace', 'scheduler_alive': self.server.scheduler.is_alive() if hasattr(self.server, 'scheduler') else False})
                elif route.path == '/api/artifact':
                    query = parse_qs(route.query)
                    path = app.artifact(query['goal'][0], query['path'][0])
                    # Always download artifacts; never execute user/agent HTML on this origin.
                    body = path.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Disposition', 'attachment; filename="artifact"')
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    name = unquote(route.path).lstrip('/') or 'index.html'
                    file = (WEB / name).resolve()
                    if not file.is_relative_to(WEB.resolve()) or not file.is_file():
                        self.send(404, {'error': '页面不存在'})
                    else:
                        self.send(200, file.read_bytes(), (mimetypes.guess_type(file.name)[0] or 'text/plain') + '; charset=utf-8')
            except PermissionError as exc:
                self.send(403, {'error': str(exc)})
            except (ValueError, KeyError) as exc:
                self.send(400, {'error': str(exc)})
            except Exception as exc:
                self.send(500, {'error': str(exc)[:500]})

        def do_POST(self):
            try:
                self.trusted(True)
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= MAX_BODY:
                    raise ValueError('请求为空或过大')
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError('请求必须是对象')
                route = urlparse(self.path).path
                result = self.action(route, payload)
                self.send(200, {'ok': True, 'result': result})
            except PermissionError as exc:
                self.send(403, {'error': str(exc)})
            except (ValueError, KeyError) as exc:
                self.send(400, {'error': str(exc)})
            except Exception as exc:
                self.send(500, {'error': str(exc)[:500]})

        def action(self, route, payload):
            if route == '/api/goals':
                return app.create_goal(payload)
            if route == '/api/goal/control':
                return app.set_enabled(payload['id'], payload['enabled'])
            if route == '/api/goal/feedback':
                import time
                goal = payload['id']
                body = text(payload.get('text'), '反馈')
                with app.lock:
                    row = app.store.get('goals', goal)
                    app.store.event(goal, 'human_feedback', body)
                    app.store.patch('goals', goal, revision=row['revision'] + 1, next_run=time.time())
                    task = app.task(goal, {'title': '评估新反馈并调整下一步 [' + uid('feedback')[-6:] + ']',
                        'acceptance': '结合这条人类反馈和已有证据调整计划，说明改变与保留的工作：' + body,
                        'skill': 'planning'}, 'human_feedback')
                app.wake.set()
                return {'todo_id': task}
            if route == '/api/members':
                return app.member(payload)
            if route == '/api/dialogue':
                return app.dialogue(payload)
            if route == '/api/respond':
                return app.respond(payload['id'], payload)
            if route == '/api/evidence':
                return app.evidence(payload['id'], payload)
            if route == '/api/upload':
                goal = payload['goal_id']
                app.store.get('goals', goal)
                name = Path(text(payload['name'], '文件名', 200)).name
                if name in ('.', '..', ''):
                    raise ValueError('无效文件名')
                content = base64.b64decode(payload['data'], validate=True)
                if len(content) > 8_000_000:
                    raise ValueError('文件大于 8 MB')
                directory = app.loop.workspace(goal) / 'uploads'
                directory.mkdir(exist_ok=True)
                if not directory.resolve().is_relative_to(app.loop.workspace(goal).resolve()):
                    raise ValueError('上传目录不能链接到工作目录之外')
                if payload.get('request_id'):
                    req = app.store.get('requests', payload['request_id'])
                    if req['goal_id'] != goal:
                        raise ValueError('文件和调用不属于同一 Goal')
                path = directory / (uid('file') + '-' + name)
                path.write_bytes(content)
                relative = path.relative_to(app.loop.workspace(goal)).as_posix()
                if payload.get('request_id'):
                    app.evidence(req['id'], {'text': '人类提交文件：' + name, 'path': relative})
                return {'path': relative}
            if route == '/api/monitors':
                return app.add_monitor(payload['goal_id'], payload)
            if route == '/api/monitor/control':
                goal, todo = payload['goal_id'], payload['id']
                app.store.get('monitors', todo)
                if payload['operation'] == 'run':
                    from service import iso
                    import time
                    app.loop.update(goal, todo, next_due_at=iso(time.time()), status='open')
                    app.store.patch('goals', goal, next_run=time.time())
                elif payload['operation'] in ('pause', 'resume'):
                    app.loop.update(goal, todo, status='blocked' if payload['operation'] == 'pause' else 'open')
                else:
                    raise ValueError('未知操作')
                app.invalidate(goal)
                app.wake.set()
                return None
            if route == '/api/lesson':
                lesson = app.store.get('lessons', payload['id'])
                active = payload.get('active', lesson['active'])
                if not isinstance(active, bool):
                    raise ValueError('经验启停参数无效')
                return app.store.patch('lessons', lesson['id'], active=active,
                                       text=text(payload.get('text', lesson['text']), '经验内容'))
            if route == '/api/settings':
                member = payload.get('local_member_id', '')
                if member:
                    app.store.get('members', member)
                enabled = payload.get('airjelly_enabled', False)
                if not isinstance(enabled, bool) or (enabled and not member):
                    raise ValueError('启用行为记录前需要选择本机成员')
                limit = payload.get('daily_model_calls', 80)
                if type(limit) is not int or not 1 <= limit <= 1000:
                    raise ValueError('每日模型调用上限需在 1–1000 之间')
                return app.store.patch('settings', 'local', airjelly_enabled=enabled,
                                       local_member_id=member, daily_model_calls=limit)
            if route == '/api/connections/check':
                if payload['provider'] == 'codex':
                    app.connections['codex'] = app.model.check()
                elif payload['provider'] == 'airjelly':
                    app.airjelly.check()
                    app.connections['airjelly'] = {'available': True, 'detail': '实例与 listEvents 授权已验证'}
                else:
                    raise ValueError('未知连接')
                return app.connections
            if route == '/api/capabilities/infer':
                # Model calls are serialized with the worker; this action schedules
                # extraction rather than starting a competing executor process.
                member = payload['member_id']
                app.store.get('members', member)
                return app.store.put('jobs', {'id': uid('job'), 'member_id': member, 'kind': 'capabilities', 'phase': 'queued'})
            raise ValueError('未知操作')
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8778)
    parser.add_argument('--codex-bin', default='codex')
    parser.add_argument('--native-dashboard-port', type=int, default=0,
                        help='Optionally serve the original LoopX Goal chat on a separate local port.')
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    # A process lock prevents two schedulers from executing the same Goal.
    lock = (args.data / 'server.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('此数据目录已有运行中的服务')
    app = Workspace(args.data, Codex(args.codex_bin))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(app))
    native = None
    if args.native_dashboard_port:
        native = subprocess.Popen([sys.executable, '-m', 'loopx.cli', '--runtime-root', str(app.loop.runtime),
            'dashboard', '--global-registry', '--port', str(args.native_dashboard_port),
            '--codex-bin', args.codex_bin, '--no-open'], cwd=REPO, stdin=subprocess.DEVNULL)
        app.native_dashboard_url = f'http://127.0.0.1:{args.native_dashboard_port}/chat/'
    worker = threading.Thread(target=app.serve_loop, name='team-goal-scheduler', daemon=True)
    server.scheduler = worker
    worker.start()
    def shutdown(*_):
        app.stop_event.set()
        app.wake.set()
        app.model.cancel()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f'Team Workspace: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    finally:
        shutdown()
        if native:
            native.terminate()
            try:
                native.wait(timeout=5)
            except subprocess.TimeoutExpired:
                native.kill()
                native.wait()
        worker.join(timeout=15)
        server.server_close()
        if not worker.is_alive():
            app.store.close()


if __name__ == '__main__':
    main()
