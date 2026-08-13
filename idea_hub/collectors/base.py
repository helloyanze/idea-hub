from dataclasses import dataclass


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

    def fetch(self) -> list[RawItem]:
        raise NotImplementedError
