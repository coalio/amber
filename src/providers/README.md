# Providers

Providers isolate model API differences from the semantic client.

## Current Shape

- `base.py`: provider protocol and request/response types.
- `gateway.py`: selects a provider by configured name.
- `openai/`: OpenAI provider implementation and model contract handling.

The configured provider currently defaults to OpenAI.

## Common Changes

- Add provider-specific API behavior under a provider folder.
- Keep semantic schemas in `src/ai/semantic/`; providers should only translate requests and responses.
- Update `ModelProviderGateway` when adding a provider name.
- Add tests for provider request shape, response parsing, and model-contract differences.
