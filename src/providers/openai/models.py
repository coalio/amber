from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OpenAIModelContract:
    prefixes: tuple[str, ...]
    unsupported_request_fields: frozenset[str] = field(default_factory=frozenset)
    supports_reasoning_effort: bool = False

    def matches(self, model: str) -> bool:
        lowered = model.lower()
        return any(lowered.startswith(prefix.lower()) for prefix in self.prefixes)


OPENAI_MODEL_CONTRACTS: tuple[OpenAIModelContract, ...] = (
    # GPT-5 family is treated as a reasoning-model family in this project.
    # Live request validation showed the configured GPT-5.4 model rejects `temperature`.
    OpenAIModelContract(
        prefixes=("gpt-5",),
        unsupported_request_fields=frozenset({"temperature"}),
        supports_reasoning_effort=True,
    ),
    # GPT-4.1 family is used here as the cheaper non-reasoning rewrite model.
    OpenAIModelContract(
        prefixes=("gpt-4.1",),
        unsupported_request_fields=frozenset(),
        supports_reasoning_effort=False,
    ),
)

DEFAULT_OPENAI_MODEL_CONTRACT = OpenAIModelContract(prefixes=("",), unsupported_request_fields=frozenset())


def get_openai_model_contract(model: str) -> OpenAIModelContract:
    for contract in OPENAI_MODEL_CONTRACTS:
        if contract.matches(model):
            return contract
    return DEFAULT_OPENAI_MODEL_CONTRACT
