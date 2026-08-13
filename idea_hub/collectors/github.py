import requests
from bs4 import BeautifulSoup

from idea_hub.collectors.base import BaseCollector, RawItem


class GithubTrendingCollector(BaseCollector):
    type = "github-trending"

    def fetch(self) -> list[RawItem]:
        url = self.source_row.get("url") or "https://github.com/trending?since=daily"
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        items = []
        for article in soup.select("article.Box-row"):
            h2 = article.select_one("h2 a")
            if not h2:
                continue
            repo = h2.get_text(" ", strip=True).replace(" ", "")
            desc = article.select_one("p")
            stars = article.select_one("span.d-inline-block.float-sm-right")
            desc_text = desc.get_text(strip=True) if desc else ""
            stars_text = stars.get_text(strip=True) if stars else ""
            items.append(RawItem(
                title=repo,
                url=f"https://github.com{h2['href']}",
                content_snapshot=f"{stars_text} {desc_text}".strip(),
                source_id=self.source_id,
            ))
        return items
