import re
from xml.etree import ElementTree as ET

from .models import SourceFeedEntry

ARTICLE_URL_PATTERN = re.compile(
    r"Article URL:\s*<a\s+href=\"([^\"]+)\"", re.IGNORECASE
)
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _extract_article_url(item: ET.Element) -> str | None:
    description = item.findtext("description") or ""
    match = ARTICLE_URL_PATTERN.search(description)
    if match:
        return match.group(1).strip()

    link = (item.findtext("link") or "").strip()
    if link and "news.ycombinator.com/item?id=" not in link:
        return link
    return None


def parse_source_feed(xml_text: str, limit: int) -> tuple[str, list[SourceFeedEntry]]:
    root = ET.fromstring(xml_text)  # noqa: S314 - trusted upstream controlled by caller
    channel = root.find("channel")
    if channel is None:
        if root.tag.endswith("feed"):
            return _parse_atom_feed(root, limit)
        raise ValueError("source feed missing channel")

    source_title = (channel.findtext("title") or "Source Feed").strip()
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


def _atom_entry_link(entry: ET.Element) -> str | None:
    # Prefer alternate links with explicit href first.
    for rel in ("alternate", ""):
        for link in entry.findall("atom:link", ATOM_NS):
            if rel and link.get("rel") != rel:
                continue
            href = (link.get("href") or "").strip()
            if href:
                return href
    id_text = (entry.findtext("atom:id", default="", namespaces=ATOM_NS) or "").strip()
    if id_text.startswith("http://") or id_text.startswith("https://"):
        return id_text
    return None


def _parse_atom_feed(root: ET.Element, limit: int) -> tuple[str, list[SourceFeedEntry]]:
    title = (root.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
    source_title = title or "Source Feed"
    entries: list[SourceFeedEntry] = []
    for entry in root.findall("atom:entry", ATOM_NS)[:limit]:
        article_url = _atom_entry_link(entry)
        if not article_url:
            continue
        pub_date = (
            entry.findtext("atom:published", default="", namespaces=ATOM_NS)
            or entry.findtext("atom:updated", default="", namespaces=ATOM_NS)
            or ""
        ).strip()
        entry_title = (
            entry.findtext("atom:title", default=article_url, namespaces=ATOM_NS) or ""
        ).strip()
        entries.append(
            SourceFeedEntry(
                title=entry_title or article_url,
                article_url=article_url,
                pub_date=pub_date or None,
            )
        )
    return source_title, entries
