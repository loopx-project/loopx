# Text statistics: Developer Book companion

[中文版](README.zh-CN.md)

This is the complete zero-permission standalone Extension used by the Developer
Book. It is a teaching package, not a built-in Capability, published PyPI
release, or entry in LoopX's default extension catalog. Generic extension
lifecycle owns activation; this package owns only its text input and result.

`characters` counts Python Unicode code points, `words` counts whitespace
chunks (not language-specific tokens), and `lines` follows `str.splitlines()`.
The decoder limits input to 65,536 bytes and text to 32,000 code points,
rejects duplicate keys and whitespace-only text, and returns fixed public error
codes. The schemas describe the wire shape; the decoder also checks encoding,
byte size, duplicate keys and Python whitespace semantics.

## Run the complete example

From a reviewed LoopX checkout root, copy the package into a new exercise
directory. `copytree` refuses an existing destination. Keep this environment
and working directory for the book's lifecycle chapter.

```bash
python3 -c "import shutil; shutil.copytree('packages/loopx-text-stats', 'standalone-extension')"
python3 -m venv .local/book-extension-venv
. .local/book-extension-venv/bin/activate
python3 -m pip install -e . -e ./standalone-extension
python3 -m unittest discover -s standalone-extension/tests -v
loopx-text-stats --doctor
loopx-text-stats < standalone-extension/examples/request.json
```

This installs the reviewed LoopX checkout and provider in an isolated Python
environment. To use an already installed release instead, install only the
provider into that chosen environment and check `loopx --version` first.
The provider and tests use the standard library and have no `[test]` extra.

For managed activation, create an isolated state directory and keep the same
variable for every command:

```bash
extension_state_dir="$(mktemp -d)"
extension_state="$extension_state_dir/state.json"
loopx extension install --manifest standalone-extension/extension.toml --state-file "$extension_state" --format json
loopx extension install --manifest standalone-extension/extension.toml --state-file "$extension_state" --execute --format json
loopx extension run loopx-text-stats --input-json standalone-extension/examples/request.json --state-file "$extension_state" --execute --format json
loopx extension disable loopx-text-stats --state-file "$extension_state" --execute --format json
```

Review the preview before the executing install. Disabled run requests must
fail. See the book for enable, upgrade, package restoration, and rollback.
The source test command, without installation, is:

```bash
python3 -m unittest discover -s packages/loopx-text-stats/tests -v
```

## What the tests establish

They cover successful results, Unicode measurement, deterministic repeats,
unknown/missing fields, wrong types/schema, duplicate keys, encoding and size
limits, doctor and one-object CLI output. A subprocess regression fixes
Python's integer conversion limit at 4,300 digits: a 5,000-digit JSON integer
must produce an error JSON object, not a traceback.

These tests are not managed-runtime lifecycle or OS sandbox qualification.
`permissions = []` does not sandbox third-party Python code. No arbitrary
file reads, network operations, credentials, Goal/Todo writes, or model calls
belong in this provider. Review implementation before installing a package.
Keep activation state, environments and generated build files out of commits.
