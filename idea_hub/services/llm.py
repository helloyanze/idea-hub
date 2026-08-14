"""DeepSeek 直调层：带重试的 JSON 对话调用。"""

from collections.abc import Callable
import json
import logging
import re

import httpx


logger = logging.getLogger(__name__)


LLM_URL = "https://api.deepseek.com/chat/completions"
LLM_MODEL = "deepseek-chat"


def _json_block_candidates(text: str):
    """从文本中按顺序提取可能的 JSON 对象或数组块。"""
    for match in re.finditer(r"[\[{]", text):
        start = match.start()
        stack = []
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "[{":
                stack.append(char)
            elif char in "]}":
                if not stack or (stack[-1] == "[" and char != "]") or (
                    stack[-1] == "{" and char != "}"
                ):
                    break
                stack.pop()
                if not stack:
                    yield text[start:index + 1]
                    break


def _parse_llm_json(raw: str):
    """宽松解析 LLM 输出中的 JSON 对象或数组。"""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("no json found in LLM output")

    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.IGNORECASE | re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    for block in _json_block_candidates(text):
        try:
            return json.loads(block)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

    raise ValueError("no json found in LLM output")


def chat_json(
    messages: list[dict],
    api_key: str,
    timeout: float = 90,
    max_retries: int = 2,
    heartbeat: Callable[[], None] | None = None,
    token_usage: dict | None = None,
) -> dict:
    """调用 DeepSeek 并宽松解析返回的 JSON。"""
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 未配置：生成任务需要 LLM key，无法降级执行")

    last_error: Exception | None = None
    total_attempts = max_retries + 1
    for attempt in range(total_attempts):
        try:
            if heartbeat is not None:
                heartbeat()
            resp = httpx.post(
                LLM_URL,
                timeout=timeout,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": LLM_MODEL,
                    "temperature": 0,
                    "messages": messages,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if token_usage is not None:
                usage = data.get("usage") or {}
                token_usage["total"] = int(usage.get("total_tokens") or 0)
            content = data["choices"][0]["message"]["content"]
            return _parse_llm_json(content)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "LLM JSON call failed (attempt %d/%d): %s",
                attempt + 1,
                total_attempts,
                exc,
            )

    if last_error is not None:
        raise last_error
    raise RuntimeError("LLM JSON call did not execute")


def chat_text(
    messages: list[dict],
    api_key: str,
    timeout: float = 90,
    max_retries: int = 2,
    heartbeat: Callable[[], None] | None = None,
    token_usage: dict | None = None,
) -> str:
    """调用 DeepSeek 并返回原始文本内容（用于正式内容生成）。"""
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 未配置：生成任务需要 LLM key，无法降级执行")

    last_error: Exception | None = None
    total_attempts = max_retries + 1
    for attempt in range(total_attempts):
        try:
            if heartbeat is not None:
                heartbeat()
            resp = httpx.post(
                LLM_URL,
                timeout=timeout,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": LLM_MODEL,
                    "temperature": 0,
                    "messages": messages,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if token_usage is not None:
                usage = data.get("usage") or {}
                token_usage["total"] = int(usage.get("total_tokens") or 0)
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            last_error = exc
            logger.warning(
                "LLM text call failed (attempt %d/%d): %s",
                attempt + 1,
                total_attempts,
                exc,
            )

    if last_error is not None:
        raise last_error
    raise RuntimeError("LLM text call did not execute")
