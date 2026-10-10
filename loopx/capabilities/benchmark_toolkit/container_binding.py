from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

BENCHMARK_EXACT_CONTAINER_BINDING_SCHEMA_VERSION = (
    "benchmark_exact_container_binding_v0"
)
DOCKER_CONTAINER_DISCOVERY_TIMEOUT_SECONDS = 30

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class DockerContainerBindingError(RuntimeError):
    """Raised when a runner cannot prove one exact Docker container binding."""


@dataclass(frozen=True, slots=True)
class DockerContainerBinding:
    """Private runner handle plus the public-safe facts that identify its selector."""

    container_name: str
    selector_sha256: str
    required_label_keys: tuple[str, ...]


def _run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        check=False,
        timeout=DOCKER_CONTAINER_DISCOVERY_TIMEOUT_SECONDS,
    )


def _validate_selector_part(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise DockerContainerBindingError("invalid_docker_container_selector")
    return value


def select_exact_docker_container(
    *,
    ancestor_image: str,
    required_labels: Mapping[str, str],
    command_runner: CommandRunner | None = None,
) -> DockerContainerBinding:
    """Resolve exactly one running container from runner-owned job identity.

    The returned container name is private runtime state. Publish only the output of
    :func:`compact_docker_container_binding_receipt`.
    """

    image = _validate_selector_part(ancestor_image)
    if not isinstance(required_labels, Mapping) or not required_labels:
        raise DockerContainerBindingError("invalid_docker_container_selector")

    labels: list[tuple[str, str]] = []
    for raw_key, raw_value in required_labels.items():
        key = _validate_selector_part(raw_key)
        value = _validate_selector_part(raw_value)
        if "=" in key:
            raise DockerContainerBindingError("invalid_docker_container_selector")
        labels.append((key, value))
    labels.sort()

    selector = {
        "ancestor_image": image,
        "required_labels": labels,
    }
    selector_sha256 = hashlib.sha256(
        json.dumps(selector, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    command = ["docker", "ps", "--filter", f"ancestor={image}"]
    for key, value in labels:
        command.extend(("--filter", f"label={key}={value}"))
    command.extend(("--format", "{{.Names}}"))

    try:
        completed = (command_runner or _run_command)(command)
    except subprocess.TimeoutExpired as error:
        raise DockerContainerBindingError(
            "docker_container_discovery_timed_out"
        ) from error
    except OSError as error:
        raise DockerContainerBindingError(
            "docker_container_discovery_failed"
        ) from error
    if completed.returncode != 0 or not isinstance(completed.stdout, str):
        raise DockerContainerBindingError("docker_container_discovery_failed")

    matches = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(matches) != 1:
        raise DockerContainerBindingError("docker_container_binding_not_exact")

    return DockerContainerBinding(
        container_name=matches[0],
        selector_sha256=selector_sha256,
        required_label_keys=tuple(key for key, _ in labels),
    )


def compact_docker_container_binding_receipt(
    binding: DockerContainerBinding,
) -> dict[str, object]:
    """Return a public-safe proof that one exact runner container was selected."""

    return {
        "schema_version": BENCHMARK_EXACT_CONTAINER_BINDING_SCHEMA_VERSION,
        "authority": "runner",
        "exact_job_binding": True,
        "match_count": 1,
        "ancestor_image_bound": True,
        "required_label_keys": list(binding.required_label_keys),
        "selector_sha256": binding.selector_sha256,
        "public_boundary": {
            "raw_container_name_recorded": False,
            "raw_container_id_recorded": False,
            "raw_label_values_recorded": False,
        },
    }
