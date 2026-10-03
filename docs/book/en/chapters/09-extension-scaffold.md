# Build a standalone Extension

Use a complete zero-permission teaching package to learn requests, responses, validation, and packaging. [Companion source](https://github.com/loopx-project/loopx/tree/main/packages/loopx-text-stats) in `packages/loopx-text-stats/` includes its manifest, Python package, two JSON Schemas, sample input, and standard-library tests.

It is not a built-in Capability or default catalog entry and does not read or write Goals. The package owns text input and results; the existing Extension lifecycle owns installation, activation, and managed calls.

## Run the complete example first

From a reviewed LoopX checkout root, copy the entire package into a new directory. `copytree` refuses an existing destination. Keep this working directory and environment for the next chapter.

```bash
python3 -c "import shutil; shutil.copytree('packages/loopx-text-stats', 'standalone-extension')"
python3 -m venv .local/book-extension-venv
. .local/book-extension-venv/bin/activate
python3 -m pip install -e . -e ./standalone-extension
python3 -m unittest discover -s standalone-extension/tests -v
loopx-text-stats --doctor
loopx-text-stats < standalone-extension/examples/request.json
```

This installs the current LoopX checkout and provider into one isolated environment. To use an installed release instead, check its version and install only the provider there. Do not mix those routes accidentally. Tests use unittest, with no `[test]` extra.

These commands install and directly invoke the package without writing LoopX activation state. Activate it once with an isolated state file in the next chapter.

## How the official scaffold differs from the finished example

To practice starting from the initial domain implementation, generate a separate starter:

```bash
loopx extension init loopx-text-stats \
  --destination text-stats-starter --execute --format json
```

The destination must not exist. The official scaffold echoes a message. This finished example changes it to text statistics and updates the decoder, schemas, example, and tests together. Generation does not build, install, or activate it.

Choose the complete example to inspect working behavior or the starter to practice contract changes. Renaming a function alone does not complete domain adaptation.

## Declare only the required lifecycle

```toml
schema_version = "loopx_extension_manifest_v0"
id = "loopx-text-stats"
version = "0.1.0"
requires_loopx_api = ">=1,<2"
permissions = []

[runtime]
protocol = "loopx_text_stats_extension_v0"
entrypoint = "loopx-text-stats"
doctor_args = ["--doctor"]
required_permissions = []
timeout_seconds = 30
```

`id` identifies the Extension, `version` participates in revision, and `requires_loopx_api` declares compatibility. Its entrypoint must resolve in LoopX's environment. Managed runtime uses `timeout_seconds`; the current contract permits 1–120 seconds.

The example declares no `provides` / `implements` or permissions, so it uses the standalone runner. Protected effects need their Capability/domain contract. Declaring permissions grants no authority and provides no OS sandbox.

## Define what the measurements mean

`characters` counts Python Unicode code points, `words` counts whitespace chunks, and `lines` follows `str.splitlines()`. Thus “你好世界” has one word here, and combining characters may contain multiple code points. This is not language-specific tokenization.

The example input is:

```json
{
  "schema_version": "loopx_text_stats_request_v0",
  "text": "LoopX keeps work explicit.\nTests verify the result."
}
```

The successful response's `result` is:

```json
{"characters": 51, "non_whitespace_characters": 44, "words": 8, "lines": 2}
```

The envelope retains `ok`, response schema, request schema, and extension identity. See `schemas/response.schema.json` for the full shape. Any outer LoopX managed receipt is distinct from the domain result.

## Why the decoder also validates

Requests must be objects with the exact schema and non-whitespace text; extra fields are rejected. JSON Schema describes the wire shape. The decoder enforces fields and also checks encoding, duplicate keys, byte size, and Python whitespace semantics.

The input ceiling is 65,536 bytes and text is limited to 32,000 code points. These are this example's limits, not global LoopX defaults.

A long integer exposes another boundary: `json.loads` may raise `ValueError` under Python's integer conversion limit even below the byte ceiling. Catching only `JSONDecodeError` leaves that request outside the uniform error response.

The implementation preserves specific `InvalidRequest` errors, then maps other parsing `ValueError` and recursion failures to `invalid_json`. Duplicate keys retain `duplicate_field`. It neither relaxes interpreter limits nor returns raw requests or parser messages.

## Tests with a concrete purpose

| Case | Expected result | Evidence boundary |
| --- | --- | --- |
| Example, Unicode, repeated input | Defined metrics and deterministic output | This provider's domain function |
| Missing/unknown fields, wrong types/schema, duplicate keys | Fixed errors without raw-value echo | Input contract and public errors |
| Byte and text boundaries | Accept within bounds, reject overflow | The example's local limits |
| 5,000-digit integer | Error JSON with no traceback on stderr | Python 3.11+ integer parsing regression |
| Doctor and CLI output | Doctor bypasses the business request; run emits one object | Real provider subprocess entrypoints |

Source and tests ship together. Tests invoke no model, external service, or live Goal. They do not qualify managed process termination, permission isolation, or upgrades. The next chapter validates lifecycle separately.

## Extend the example coherently

Define the new input and verifiable result, then update decoder, schemas, example, and tests together. Adding external state or permission needs requires reassessing placement; a permission field cannot preserve zero-permission runner assumptions.

The [English README](https://github.com/loopx-project/loopx/blob/main/packages/loopx-text-stats/README.md) and [Chinese README](https://github.com/loopx-project/loopx/blob/main/packages/loopx-text-stats/README.zh-CN.md) give the same complete steps. There is no separate exercise repository or missing file to reconstruct from conversation.
