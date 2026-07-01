"""材料取込パイプライン(Phase6) — SEC EDGAR + RSS + 手動入力を統合する。

処理フロー(1件のMaterialDraftあたり、Opus設計ドキュメント§8のパイプライン準拠):
  1. company/topic抽出 → dedup_bucket計算
  2. 重複検知(dedup.py) → 一致なし: 新規 / duplicate,confirm: 抑制 / update,supersede: 既存ID再利用
  3. material_id確定(新規時のみ generate_material_id)
  4. 鮮度スコア算出 → 通知可否判定(§7、1日1回実行への緩和ルール込み)
  5. SQLiteへ登録 → 最後に呼び出し側で dump_to_jsonl してコミット対象を更新

news/IR取得器そのもの(sec_edgar.py, rss_fetcher.py)とDB永続化(db.py)の橋渡し役。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from src.data_sources.rss_fetcher import RssSourceConfig, fetch_all_configured_sources
from src.data_sources.sec_edgar import fetch_edgar_for_companies
from src.materials.db import (
    dump_to_jsonl,
    ensure_source_exists,
    list_materials,
    rebuild_from_jsonl,
    upsert_material,
)
from src.materials.dedup import (
    content_hash,
    dedup_bucket,
    detect_duplicate_material,
    has_new_fact_keywords,
    should_renotify,
)
from src.materials.freshness import (
    compute_freshness_score,
    is_detection_delayed,
    is_notification_allowed,
)
from src.materials.manual_input import read_manual_materials
from src.materials.material_id import (
    _classify_topic,
    _normalize_company,
    extract_company_alias,
    generate_material_id,
)
from src.materials.models import Material, MaterialDraft, Source
from src.materials.source_rank import assign_source_rank
from src.materials.taxonomy import FreshnessScore, NotificationStatus, SourceRank, SourceType

logger = logging.getLogger(__name__)


def _parse_rss_date(raw: str) -> datetime | None:
    """RSS(RFC822)/Atom(ISO8601)いずれの日付形式も試す。失敗時はNone。"""
    raw = raw.strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        logger.debug("RSS日付解析失敗: %r", raw)
        return None


def _edgar_hits_to_drafts(
    hits: list[dict], detected_at: datetime
) -> list[MaterialDraft]:
    """EDGAR全文検索ヒット → MaterialDraft。

    注意: EDGAR全文検索は「クエリ企業名を本文中に含む任意の開示」を返す
    (例: Teslaで検索すると、Teslaを一切関係のない企業の10-Kが競合他社
    比較として言及しているだけでもヒットする)。company_hint には検索queryではなく
    実際の提出者(display_names)を入れ、material_id が誤ってquery企業に
    紐づかないようにする。
    """
    drafts = []
    for h in hits:
        filer_name = h.get("title") or "Unknown Filer"
        form = h.get("form_type") or ""
        filed = h.get("filed_at")
        published = None
        if filed:
            try:
                published = datetime.fromisoformat(str(filed)).replace(tzinfo=UTC)
            except ValueError:
                published = None
        drafts.append(MaterialDraft(
            title=f"{filer_name} {form}".strip(),
            summary=f"accession={h.get('accession_no')} matched_query={h.get('query_company')}",
            source_id="sec_edgar",
            source_rank=SourceRank.A,
            published_at=published,
            detected_at=detected_at,
            company_hint=filer_name,
        ))
    return drafts


def _rss_entries_to_drafts(
    source: RssSourceConfig, entries: list[dict], detected_at: datetime
) -> list[MaterialDraft]:
    try:
        stype = SourceType(source.source_type)
    except ValueError:
        stype = SourceType.UNKNOWN
    rank = assign_source_rank(stype, source.is_customer_official)
    # company_ir フィードは通常その企業単独のIRなので display_name を確実な
    # company_hint として使える(wire/gov等の複数企業横断フィードでは使わない)。
    company_hint = source.display_name if stype == SourceType.COMPANY_IR else None

    drafts = []
    for e in entries:
        title = (e.get("title") or "").strip()
        if not title:
            continue
        drafts.append(MaterialDraft(
            title=title,
            summary=e.get("summary", ""),
            source_id=source.source_id,
            source_rank=rank,
            published_at=_parse_rss_date(e.get("published", "")),
            detected_at=detected_at,
            is_customer_official=source.is_customer_official,
            company_hint=company_hint,
        ))
    return drafts


def ingest_draft(conn: sqlite3.Connection, draft: MaterialDraft) -> Material | None:
    """1件のMaterialDraftを重複検知→鮮度判定→登録まで処理する。

    通知抑制(§6 duplicate/confirm)の場合は登録せず None を返す。
    """
    ensure_source_exists(conn, Source(
        source_id=draft.source_id,
        display_name=draft.source_id,
        source_rank=draft.source_rank.value,
        source_type="unknown",
        is_customer_official=draft.is_customer_official,
        created_at=draft.detected_at.isoformat(),
    ))

    existing = list_materials(conn)
    existing_ids = {m.material_id for m in existing}

    # company_hint(実際の発信元が確実な場合)を最優先し、無ければ本文からの
    # 別名抽出、それも失敗したら source_id にフォールバックする。
    company = (
        draft.company_hint
        or extract_company_alias(f"{draft.title} {draft.summary}")
        or draft.source_id
    )
    event_date = (draft.published_at or draft.detected_at).date()
    company_tok = _normalize_company(company)
    topic_tok = _classify_topic(draft.title, draft.summary)
    bucket = dedup_bucket(company_tok, topic_tok, event_date)

    match = detect_duplicate_material(draft, bucket, existing)
    freshness = compute_freshness_score(draft.published_at, draft.detected_at)
    delayed = is_detection_delayed(draft.published_at, draft.detected_at)
    now_iso = draft.detected_at.isoformat()

    if match is not None:
        if not should_renotify(match):
            logger.info(
                "重複のため取込スキップ: title=%r matched=%s relation=%s",
                draft.title[:50], match.matched_id, match.relation.value,
            )
            return None
        # update/supersede: classify_relation内で既に新事実キーワード or
        # 上位ソースによる上書きが確認された結果なので、新事実として扱う。
        material_id = match.matched_id
        previous_relation: str | None = json.dumps(
            {"prev_id": match.matched_id, "relation": match.relation.value},
            ensure_ascii=False,
        )
        new_fact_flag = True
    else:
        material_id = generate_material_id(
            company, draft.title, event_date, existing_ids, draft.summary
        )
        previous_relation = None
        # 初見の材料でも、鮮度が古い(§7 C)場合は自動収集の後追い検知の
        # 可能性があるため、新事実キーワードが無ければ「古いニュースの
        # 蒸し返し」扱いにする。鮮度が新しければ無条件に新事実として扱う。
        new_fact_flag = (
            freshness != FreshnessScore.C
            or has_new_fact_keywords(draft.title)
            or has_new_fact_keywords(draft.summary)
        )

    allowed, reason = is_notification_allowed(freshness, new_fact_flag)

    m = Material(
        material_id=material_id,
        title=draft.title,
        summary=draft.summary,
        source_id=draft.source_id,
        source_rank=draft.source_rank.value,
        published_at=draft.published_at.isoformat() if draft.published_at else None,
        first_detected_at=now_iso,
        related_tickers=draft.related_tickers,
        affected_factors=draft.affected_factors,
        new_fact_flag=new_fact_flag,
        notification_status=(
            NotificationStatus.PENDING.value if allowed else NotificationStatus.SUPPRESSED.value
        ),
        previous_material_relation=previous_relation,
        freshness_score=freshness.value,
        detection_delayed=delayed,
        content_hash=content_hash(draft.title),
        dedup_bucket=bucket,
        created_at=now_iso,
        updated_at=now_iso,
    )
    upsert_material(conn, m)
    logger.info(
        "材料登録: id=%s rank=%s freshness=%s allowed=%s(%s)",
        material_id, draft.source_rank.value, freshness.value, allowed, reason,
    )
    return m


def run_ingest(
    db_path: str,
    dump_dir: str,
    company_queries: list[str] | None = None,
    edgar_forms: list[str] | None = None,
) -> dict[str, int]:
    """SEC EDGAR + RSS + 手動入力を取込み、DBへ登録してJSONLへ書き戻す。

    どのソースも設定/データが無ければ静かにスキップする(クラッシュしない)。
    戻り値は各ソースの新規登録件数のサマリ。
    """
    from src.materials.db import get_connection

    conn = get_connection(db_path)
    rebuild_from_jsonl(conn, dump_dir)

    now = datetime.now(UTC)
    counts = {"sec_edgar": 0, "rss": 0, "manual": 0}

    if company_queries:
        try:
            hits = fetch_edgar_for_companies(company_queries, forms=edgar_forms)
            for draft in _edgar_hits_to_drafts(hits, now):
                if ingest_draft(conn, draft) is not None:
                    counts["sec_edgar"] += 1
        except Exception as exc:
            logger.warning("SEC EDGAR取込失敗: %s", exc)

    try:
        for source, entries in fetch_all_configured_sources():
            for draft in _rss_entries_to_drafts(source, entries, now):
                if ingest_draft(conn, draft) is not None:
                    counts["rss"] += 1
    except Exception as exc:
        logger.warning("RSS取込失敗: %s", exc)

    try:
        for draft in read_manual_materials(detected_at=now):
            if ingest_draft(conn, draft) is not None:
                counts["manual"] += 1
    except Exception as exc:
        logger.warning("手動材料取込失敗: %s", exc)

    dump_to_jsonl(conn, dump_dir)
    conn.close()
    logger.info("材料取込完了: %s", counts)
    return counts
