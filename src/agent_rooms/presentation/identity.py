from typing import Any

_STATE_KEY = "agent"


def agent_of(request_or_scope: Any) -> str:
    scope = request_or_scope if isinstance(request_or_scope, dict) else request_or_scope.scope
    agent: str = scope["state"][_STATE_KEY]
    return agent


def bind_agent(scope: Any, agent: str) -> None:
    scope.setdefault("state", {})[_STATE_KEY] = agent
