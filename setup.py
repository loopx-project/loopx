"""Require qualified assets in distributions, while editable source setup stays cheap."""

from pathlib import Path
import importlib.util
import shutil
import sys
from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.bdist_wheel import bdist_wheel
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


def clear_built_package(directory):
    stale = Path(directory) / "loopx"
    source = (Path(__file__).parent / "loopx").resolve()
    destination = stale.resolve()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise RuntimeError("build destination must be outside the LoopX source package")
    if stale.exists():
        shutil.rmtree(stale)


class BuildWithFrontend(build_py):
    def run(self):
        if not self.editable_mode:
            verify_frontend()
            # setuptools copies changed files but does not remove deleted modules or
            # package data. Rebuild only our package, including qualified asset history.
            clear_built_package(self.build_lib)
        super().run()


class WheelWithFrontend(bdist_wheel):
    def run(self):
        if not self.skip_build:
            # Validate/build before clearing staging, and let setuptools reuse that
            # completed command. --skip-build still uses the explicitly selected build.
            self.run_command("build")
        # Failed or --keep-temp builds can retain a second copy of deleted files.
        clear_built_package(self.bdist_dir)
        super().run()


class SourceWithFrontend(sdist):
    def run(self):
        verify_frontend()
        super().run()


setup(
    cmdclass={
        "build_py": BuildWithFrontend,
        "bdist_wheel": WheelWithFrontend,
        "sdist": SourceWithFrontend,
    }
)
