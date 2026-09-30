"""Require qualified assets in distributions, while editable source setup stays cheap."""

from pathlib import Path
import importlib.util
import shutil
import sys
from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist


def verify_frontend():
    root = Path(__file__).parent
    # Same reason as scripts/chat_bundle.py: the contract delegates its digest shape to
    # the shared owner, and a source distribution is verified before LoopX is installed.
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(
        "chat_bundle_contract", root / "loopx/presentation/chat_bundle.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.validate_bundle(root / "loopx/web/chat", source_root=root)


class BuildWithFrontend(build_py):
    def run(self):
        if not self.editable_mode:
            verify_frontend()
            stale = Path(self.build_lib) / "loopx/web/chat"
            if stale.exists():
                shutil.rmtree(stale)
        super().run()


class SourceWithFrontend(sdist):
    def run(self):
        verify_frontend()
        super().run()


setup(cmdclass={"build_py": BuildWithFrontend, "sdist": SourceWithFrontend})
