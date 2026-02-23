from dataclasses import dataclass


@dataclass
class SourceFeedEntry:
    title: str
    article_url: str
    pub_date: str | None


@dataclass
class WrappedFeedItem:
    title: str
    source_url: str
    pub_date: str | None
    content_html: str
