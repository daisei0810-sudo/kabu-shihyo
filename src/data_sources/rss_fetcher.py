"""汎用 RSS/Atom fetcher(§4 Phase1: 企業IR・政府機関の材料取得)。

設計方針(重要):
  企業IR・政府機関の具体的なRSS URLは、このコードにハードコードしない。
  URLは時間とともに変わり、未検証のURLを埋め込むと静かに壊れた状態で
  運用され続けるリスクがある(取得0件のまま気づかれない、等)。
  代わりに config/rss_sources.csv を正とし、運用者(ユーザー)が実際に
  動作確認したフィードURLのみを追加する運用にする。

このモジュール自体は feedparser 等の追加依存を避けるため、
標準ライブラリの xml.etree.ElementTree で RSS 2.0 / Atom を解析する。
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import requests

logger = logging.getLogger(__name__)

RSS_SOURCES_CONFIG = "config/rss_sources.csv"

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


@dataclass
class RssSourceConfig:
    """config/rss_sources.csv の1行。"""

    source_id: str
    url: str
    source_type: str          # SourceType の値(company_ir/gov/wire等)
    display_name: str
    is_customer_official: bool = False


def load_rss_sources(path: str = RSS_SOURCES_CONFIG) -> list[RssSourceConfig]:
    """config/rss_sources.csv を読み込む。存在しない/空なら空リスト。"""
    p = Path(path)
    if not p.exists():
        logger.info("rss_sources.csv が存在しません(%s)。RSS取得をスキップします。", path)
        return []
    sources: list[RssSourceConfig] = []
    with p.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = (row.get("url") or "").strip()
            if not url:
                continue
            sources.append(RssSourceConfig(
                source_id=row["source_id"].strip(),
                url=url,
                source_type=row.get("source_type", "unknown").strip(),
                display_name=row.get("display_name", row["source_id"]).strip(),
                is_customer_official=row.get("is_customer_official", "").strip().lower()
                in ("1", "true", "yes"),
            ))
    return sources


def _local_tag(tag: str) -> str:
    """名前空間付きXMLタグからローカル名を取り出す({ns}tag -> tag)。"""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_rss_or_atom(xml_text: str) -> list[dict[str, Any]]:
    """RSS 2.0 の <item> または Atom の <entry> を最小限のフィールドで抽出する。"""
    try:
        root = ElementTree.fromstring(xml_text)  # noqa: S314 (信頼済みではない外部XMLだが構造抽出のみ)
    except ElementTree.ParseError as exc:
        logger.warning("RSS/Atom parse failed: %s", exc)
        return []

    items: list[dict[str, Any]] = []
    for elem in root.iter():
        local = _local_tag(elem.tag)
        if local not in ("item", "entry"):
            continue
        entry: dict[str, Any] = {
            "title": "", "link": "", "summary": "", "published": "", "guid": "",
        }
        for child in elem:
            ctag = _local_tag(child.tag)
            text = (child.text or "").strip()
            if ctag == "title":
                entry["title"] = text
            elif ctag == "link":
                entry["link"] = child.attrib.get("href", text)
            elif ctag in ("description", "summary", "content"):
                entry["summary"] = text
            elif ctag in ("pubDate", "published", "updated"):
                entry["published"] = text
            elif ctag in ("guid", "id"):
                entry["guid"] = text
        if entry["title"]:
            items.append(entry)
    return items


def fetch_rss(url: str, timeout: int = 20) -> list[dict[str, Any]]:
    """RSS/Atomフィードを取得しエントリのリストを返す。失敗時は空リスト。"""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "kabu-shihyo-tool/1.0"})
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("RSS fetch failed url=%s err=%s", url, exc)
        return []
    return _parse_rss_or_atom(resp.text)


def fetch_all_configured_sources(
    config_path: str = RSS_SOURCES_CONFIG,
) -> list[tuple[RssSourceConfig, list[dict[str, Any]]]]:
    """config/rss_sources.csv の全ソースを取得する。"""
    sources = load_rss_sources(config_path)
    results: list[tuple[RssSourceConfig, list[dict[str, Any]]]] = []
    for src in sources:
        entries = fetch_rss(src.url)
        results.append((src, entries))
        if not entries:
            logger.info("source=%s: 0件(URL未検証または一時的失敗の可能性)", src.source_id)
    return results
