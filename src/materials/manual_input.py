"""手動材料投入(§6/§8: Reuters/Bloomberg等 source_rank=B の大手報道向け)。

無料APIが存在しない大手報道(Reuters/Bloomberg等)は自動取得できないため、
data/materials_manual/pending.csv にユーザーが手動で追記する運用で補う。
このファイルは追記式で運用してよい(取込パイプラインは毎回全件を読み、
既存材料との重複検知(dedup.py)で二重登録を防ぐため、行を消す必要はない)。
"""

from __future__ import annotations

import csv
import logging
from datetime import UTC, datetime
from pathlib import Path

from src.materials.models import MaterialDraft
from src.materials.source_rank import assign_source_rank
from src.materials.taxonomy import SourceRank, SourceType

logger = logging.getLogger(__name__)

PENDING_MANUAL_PATH = "data/materials_manual/pending.csv"


def _parse_datetime(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        logger.warning("手動材料の published_at 解析失敗: %r", value)
        return None


def _split_list(value: str) -> list[str]:
    return [v.strip() for v in value.split(";") if v.strip()]


def read_manual_materials(
    path: str = PENDING_MANUAL_PATH,
    detected_at: datetime | None = None,
) -> list[MaterialDraft]:
    """data/materials_manual/pending.csv を読み込み MaterialDraft のリストを返す。

    ファイルが存在しない/空の場合は空リスト(クラッシュしない)。
    """
    p = Path(path)
    if not p.exists():
        return []

    now = detected_at or datetime.now(UTC)
    drafts: list[MaterialDraft] = []

    with p.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = (row.get("title") or "").strip()
            if not title:
                continue
            source_type_raw = (row.get("source_type") or "wire").strip()
            try:
                source_type = SourceType(source_type_raw)
            except ValueError:
                logger.warning("未知の source_type=%r → wire として扱う", source_type_raw)
                source_type = SourceType.WIRE

            is_customer_official = (row.get("is_customer_official") or "").strip().lower() in (
                "1", "true", "yes",
            )
            rank: SourceRank = assign_source_rank(source_type, is_customer_official)

            drafts.append(MaterialDraft(
                title=title,
                summary=(row.get("summary") or "").strip(),
                source_id=(row.get("display_name") or source_type_raw).strip(),
                source_rank=rank,
                published_at=_parse_datetime(row.get("published_at") or ""),
                detected_at=now,
                related_tickers=_split_list(row.get("related_tickers") or ""),
                affected_factors=_split_list(row.get("affected_factors") or ""),
                is_customer_official=is_customer_official,
            ))
    return drafts
