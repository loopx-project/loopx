"""Zero-Any typed dict views and contracts for Antigravity callers."""

from __future__ import annotations

from typing import Literal, Mapping, Sequence, TypeAlias, TypedDict, cast, overload

JsonDict: TypeAlias = dict[str, object]
JsonMap: TypeAlias = Mapping[str, object]
JsonSeq: TypeAlias = Sequence[object]
IndexedStep: TypeAlias = tuple[int, JsonDict]


class AntigravityStepMetadataDict(TypedDict, total=False):
    createdAt: str
    executionId: str
    sourceTrajectoryStepInfo: dict[str, int]


class AntigravityStepDict(TypedDict, total=False):
    type: str
    status: str
    metadata: AntigravityStepMetadataDict
    plannerResponse: dict[str, object]
    runCommand: dict[str, object]
    viewFile: dict[str, object]
    userInput: dict[str, object]
    errorMessage: dict[str, object]
    generic: dict[str, object]
    checkpoint: dict[str, object]
    systemMessage: dict[str, object]


class AntigravityTrajectoryDict(TypedDict, total=False):
    cascadeId: str
    steps: list[AntigravityStepDict]


class AntigravityRpcResponseDict(TypedDict, total=False):
    trajectory: AntigravityTrajectoryDict
    steps: list[AntigravityStepDict]
    numTotalSteps: int
    status: str
    cascadeId: str
    clientModelConfigs: list[dict[str, object]]
    userStatus: dict[str, object]
    trajectorySummaries: dict[str, dict[str, object]]


class AntigravityReasoningEffortDict(TypedDict, total=False):
    reasoning_effort: str


class AntigravityModelOptionDict(TypedDict, total=False):
    id: str
    display_name: str
    description: str
    is_default: bool
    default_reasoning_effort: str
    supported_reasoning_efforts: list[AntigravityReasoningEffortDict]


class AntigravityEndpointDict(dict[str, object]):
    """Typed dict view over discovered Antigravity endpoints."""

    @overload
    def __getitem__(self, key: Literal["url", "csrf_token", "source"]) -> str: ...
    @overload
    def __getitem__(self, key: Literal["pid"]) -> int | None: ...
    @overload
    def __getitem__(self, key: Literal["model_config_data"]) -> AntigravityRpcResponseDict: ...
    @overload
    def __getitem__(self, key: str) -> object: ...

    def __getitem__(self, key: str) -> object:
        return super().__getitem__(key)


class AntigravityParsedResponseDict(dict[str, object]):
    """Typed dict view over parsed assistant responses."""

    @overload
    def __getitem__(self, key: Literal["message"]) -> str: ...
    @overload
    def __getitem__(self, key: str) -> object: ...

    def __getitem__(self, key: str) -> object:
        return super().__getitem__(key)


class AntigravityCompletedResultDict(dict[str, object]):
    """Typed dict view over `completed_result_observation` outputs."""

    @overload
    def __getitem__(
        self,
        key: Literal[
            "thread_id",
            "request_ref",
            "execution_id",
            "input_created_at",
            "input_sha256",
            "answer_sha256",
            "observation_source",
            "raw_response",
        ],
    ) -> str: ...
    @overload
    def __getitem__(
        self, key: Literal["start_step_index", "end_step_index", "total_steps"],
    ) -> int: ...
    @overload
    def __getitem__(
        self, key: Literal["latest_input_verified", "normal_terminal_verified"],
    ) -> bool: ...
    @overload
    def __getitem__(self, key: Literal["response"]) -> AntigravityParsedResponseDict: ...
    @overload
    def __getitem__(self, key: Literal["retained_result"]) -> JsonDict: ...
    @overload
    def __getitem__(self, key: str) -> object: ...

    def __getitem__(self, key: str) -> object:
        return super().__getitem__(key)


class AntigravityInterruptedAnswerDict(dict[str, object]):
    """Typed dict view over `interrupted_answer_observation` outputs."""

    @overload
    def __getitem__(
        self,
        key: Literal[
            "thread_id",
            "request_ref",
            "execution_id",
            "input_sha256",
            "input_created_at",
        ],
    ) -> str: ...
    @overload
    def __getitem__(
        self, key: Literal["start_step_index", "end_step_index", "total_steps"],
    ) -> int: ...
    @overload
    def __getitem__(
        self, key: Literal["read_only_review", "no_final_answer", "effects_observed"],
    ) -> bool: ...
    @overload
    def __getitem__(self, key: Literal["visible_results"]) -> list[JsonDict]: ...
    @overload
    def __getitem__(self, key: str) -> object: ...

    def __getitem__(self, key: str) -> object:
        return super().__getitem__(key)


def as_rpc_response(value: JsonDict) -> AntigravityRpcResponseDict:
    return cast(AntigravityRpcResponseDict, value)


def as_endpoint_dict(value: JsonDict) -> AntigravityEndpointDict:
    return cast(AntigravityEndpointDict, value)


def as_completed_result(value: JsonDict) -> AntigravityCompletedResultDict:
    return cast(AntigravityCompletedResultDict, value)


def as_interrupted_answer(value: JsonDict) -> AntigravityInterruptedAnswerDict:
    return cast(AntigravityInterruptedAnswerDict, value)
