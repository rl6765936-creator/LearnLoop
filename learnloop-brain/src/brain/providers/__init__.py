"""AI provider integration (Anthropic, OpenAI, Gemini) behind the existing ModelGateway.

Local development only: the default transport blocks every network call.
Entry point for the application: `build_gateway(load_config())`.
"""

from brain.providers.config import ProviderConfig, build_gateway, from_dict, load_config  # noqa: F401
