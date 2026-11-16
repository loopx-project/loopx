"""Select migrated scheduler cases in the default Python CI shards."""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SELECTED = frozenset((_ROOT / "w07_migrated_nodes.txt").read_text(encoding="utf-8").splitlines())
_MODULES = frozenset(node.split("::", 1)[0] for node in _SELECTED)
_REPLACED = frozenset(node.replace("_w07_migrated.py::", ".py::", 1) for node in _SELECTED)


def pytest_collection_modifyitems(config, items):
    retained, deselected = [], []
    for item in items:
        node = item.nodeid.replace(chr(92), "/")
        module = node.split("::", 1)[0]
        if node in _REPLACED or (module in _MODULES and node not in _SELECTED):
            deselected.append(item)
        else:
            retained.append(item)
    config.hook.pytest_deselected(items=deselected)
    items[:] = retained
