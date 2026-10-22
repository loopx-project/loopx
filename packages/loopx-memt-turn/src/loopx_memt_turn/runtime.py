"""Only this boundary imports the separately licensed MemT client."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path

from .config import AdapterError, Config, decode_document


def dependency_status() -> dict:
    from importlib.util import find_spec

    return {name: find_spec(name) is not None for name in ("memt_sglang", "transformers")}


@contextmanager
def open_session(config: Config, session_options: dict):
    if not all(dependency_status().values()):
        raise AdapterError("install_separately_licensed_memt_client")
    with (Path(config.tokenizer) / "config.json").open("rb") as stream:
        model_config = decode_document(stream.read(256_001))
    if model_config.get("model_type") != "memt_qwen3_5_moe":
        raise AdapterError("only_memt_nano_is_supported")
    from transformers import AutoTokenizer
    from memt_sglang.client import MemTSession, NativeClient, load_chat_format

    tokenizer = AutoTokenizer.from_pretrained(config.tokenizer, local_files_only=True, trust_remote_code=False)
    chat_format, parser = load_chat_format(config.tokenizer, tokenizer=tokenizer,
                                          chat_template_kwargs={"enable_thinking": False})
    client = NativeClient(config.base_url, api_key=os.environ.get("MEMT_API_KEY"), timeout=config.timeout_seconds)
    # shortcut: SDK snapshot identity omits weight contents; use a new state_dir when replacing a checkpoint.
    with MemTSession(client, chat_format, parser, **session_options) as session:
        yield session
