from src.logging_config import DEFAULT_QUIET_LOGGERS


def test_litellm_loggers_are_quiet_by_default() -> None:
    assert {"LiteLLM", "LiteLLM Router", "LiteLLM Proxy"}.issubset(
        set(DEFAULT_QUIET_LOGGERS)
    )
