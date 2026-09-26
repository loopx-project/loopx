"""Read-only selected-instance timeline adapter. Credentials never leave this process."""
import json
import os
import time
import urllib.request
from pathlib import Path


class AirJelly:
    def __init__(self, base=None):
        self.base = Path(base or Path.home() / 'Library/Application Support/AirJelly')

    def runtime(self):
        directory = self.base / 'context-instances'
        if not directory.exists():
            raise ValueError('请打开 AirJelly 并启用 Context 服务')
        selection_file = directory / 'selection.json'
        selection = json.loads(selection_file.read_text()) if selection_file.exists() else {'mode': 'auto'}
        if selection.get('mode') not in ('auto', 'instance'):
            raise ValueError('请在 AirJelly 设置中选择 Context Source')
        live = []
        for file in directory.glob('*.json'):
            if file.name == 'selection.json':
                continue
            try:
                row = json.loads(file.read_text())
                if row.get('schema_version') != '1' or not isinstance(row.get('pid'), int) or row['pid'] <= 0:
                    continue
                os.kill(row['pid'], 0)
                live.append(row)
            except (ValueError, OSError, KeyError):
                continue
        if selection['mode'] == 'instance':
            live = [r for r in live if r.get('instance_id') == selection.get('instance_id')]
        if len(live) != 1:
            raise ValueError('AirJelly 实例不可唯一确定，请在设置中选择 Context Source')
        selected = live[0]
        runtime_path = Path(selected['runtime_path'])
        if not runtime_path.is_absolute():
            raise ValueError('AirJelly 实例配置无效')
        context = json.loads(runtime_path.read_text())
        runtime = json.loads((self.base / 'runtime.json').read_text())
        for key in ('instance_id', 'pid'):
            if context.get(key) != selected.get(key) or runtime.get(key) != selected.get(key):
                raise ValueError('AirJelly 时间线与选定实例不一致')
        if not isinstance(runtime.get('port'), int) or not 0 < runtime['port'] < 65536 or not runtime.get('token'):
            raise ValueError('AirJelly 连接配置无效')
        return runtime

    def request(self, runtime, route, data=None):
        request = urllib.request.Request(f"http://127.0.0.1:{runtime['port']}{route}",
            data=json.dumps(data).encode() if data else None,
            headers={'Authorization': 'Bearer ' + runtime['token'], 'Content-Type': 'application/json'})
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise ValueError('AirJelly 不允许重定向')
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            payload = json.loads(response.read(4_000_001))
        if payload.get('ok') is False:
            raise ValueError('AirJelly 接口未授权或读取失败')
        return payload

    def check(self):
        runtime = self.runtime()
        health = self.request(runtime, '/health')
        caps = self.request(runtime, '/capabilities').get('data', {})
        if not health.get('ok') or health.get('instance_id') != runtime['instance_id']:
            raise ValueError('AirJelly 实例校验失败')
        if 'listEvents' not in caps.get('methods', []):
            raise ValueError('AirJelly 未授权 listEvents')
        return runtime

    def events(self, start, end):
        if start >= end or end - start > 86_400_000:
            raise ValueError('时间范围需在一天内')
        runtime = self.check()
        rows = self.request(runtime, '/rpc', {'method': 'listEvents', 'args': [start, end]}).get('data')
        if not isinstance(rows, list):
            raise ValueError('AirJelly 事件格式无效')
        events = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str):
                continue
            stamp = row.get('start_time')
            if not isinstance(stamp, (int, float)) or not start <= stamp <= end:
                continue
            events.append({'source_id': row['id'], 'instance_id': runtime['instance_id'],
                           'title': str(row.get('title', ''))[:500], 'content': str(row.get('content', ''))[:12000],
                           'truncated': len(str(row.get('content', ''))) > 12000,
                           'app': str(row.get('app_name', ''))[:200], 'at': stamp / 1000})
        return events
