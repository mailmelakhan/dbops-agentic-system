"""Fail at startup, not on the first chat question, when the configured LLM has no key."""

import os
from collections.abc import Mapping

import litellm

# LiteLLM's validate_environment does not know DeepSeek's key, so these are checked directly.
_KEY_VARS = {"deepseek": "DEEPSEEK_API_KEY"}
# Providers that sign in through a cloud credential chain (AWS profile, GCP ADC), which a key check can't see.
_CREDENTIAL_CHAIN = {"bedrock", "bedrock_converse", "sagemaker", "vertex_ai"}


def check_llm_credentials(model: str, api_key: str, env: Mapping[str, str] = os.environ) -> None:
    if api_key:
        return
    provider = model.split("/", 1)[0]
    if provider in _CREDENTIAL_CHAIN:
        return
    if provider in _KEY_VARS:
        missing = [] if env.get(_KEY_VARS[provider]) else [_KEY_VARS[provider]]
    else:
        missing = litellm.validate_environment(model).get("missing_keys") or []
    if missing:
        raise RuntimeError(f"LLM_MODEL {model} needs {', '.join(missing)} (or set LLM_API_KEY)")
