from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from .models import WrappedFeedItem

CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
ET.register_namespace("content", CONTENT_NS)


def render_wrapped_rss(
    source_url: str, source_title: str, items: list[WrappedFeedItem]
) -> str:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = f"{source_title} (fulltext wrapper)"
    ET.SubElement(channel, "link").text = source_url
    ET.SubElement(
        channel, "description"
    ).text = "HNRSS feed with full article content extracted via article-extractor."
    ET.SubElement(channel, "lastBuildDate").text = datetime.now(UTC).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )

    for item in items:
        out = ET.SubElement(channel, "item")
        ET.SubElement(out, "title").text = item.title
        ET.SubElement(out, "link").text = item.source_url
        ET.SubElement(out, "guid", {"isPermaLink": "true"}).text = item.source_url
        if item.pub_date:
            ET.SubElement(out, "pubDate").text = item.pub_date

        ET.SubElement(out, "description").text = (
            f'<p>Source URL: <a href="{item.source_url}">{item.source_url}</a></p>'
            f"{item.content_html}"
        )
        ET.SubElement(out, f"{{{CONTENT_NS}}}encoded").text = item.content_html

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True).decode("utf-8")
