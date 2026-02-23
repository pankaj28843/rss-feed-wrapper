import re
from xml.etree import ElementTree as ET

from .models import SourceFeedEntry

ARTICLE_URL_PATTERN = re.compile(
    r"Article URL:\s*<a\s+href=\"([^\"]+)\"", re.IGNORECASE
)


def _extract_article_url(item: ET.Element) -> str | None:
    description = item.findtext("description") or ""
    match = ARTICLE_URL_PATTERN.search(description)
    if match:
        return match.group(1).strip()

    link = (item.findtext("link") or "").strip()
    if link and "news.ycombinator.com/item?id=" not in link:
        return link
    return None


def parse_hnrss(xml_text: str, limit: int) -> tuple[str, list[SourceFeedEntry]]:
    root = ET.fromstring(xml_text)  # noqa: S314 - trusted upstream controlled by caller
    channel = root.find("channel")
    if channel is None:
        raise ValueError("source feed missing channel")

    source_title = (channel.findtext("title") or "HNRSS").strip()
    entries: list[SourceFeedEntry] = []
    for item in channel.findall("item")[:limit]:
        article_url = _extract_article_url(item)
        if not article_url:
            continue
        entries.append(
            SourceFeedEntry(
                title=(item.findtext("title") or article_url).strip(),
                article_url=article_url,
                pub_date=(item.findtext("pubDate") or "").strip() or None,
            )
        )
    return source_title, entries
