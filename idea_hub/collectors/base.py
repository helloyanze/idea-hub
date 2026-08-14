import json
from dataclasses import dataclass


class CollectorError(Exception):
    pass


@dataclass
class RawItem:
    title: str
    url: str
    content_snapshot: str
    source_id: int


class BaseCollector:
    """来源收集器抽象基类。type 为 registry 键（对应 sources.type）。"""

    type: str = ""

    def __init__(self, source_row: dict):
        self.source_row = source_row
        self.source_id = source_row["id"]

    def load_channel_config(self) -> dict:
        """解析 sources.channel_config JSON；缺失/非法时返回空 dict。"""
        try:
            config = json.loads(self.source_row.get("channel_config") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return config if isinstance(config, dict) else {}

    def build_headers(self, base_headers: dict | None = None) -> dict:
        """合并 channel_config.headers（如 {"Cookie": "..."}）到默认请求头。"""
        headers = dict(base_headers or {})
        extra = self.load_channel_config().get("headers")
        if isinstance(extra, dict):
            headers.update({str(k): str(v) for k, v in extra.items()})
        return headers

    def fetch(self) -> list[RawItem]:
        raise NotImplementedError
