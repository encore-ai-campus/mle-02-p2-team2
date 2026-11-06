"""사용자 화면의 '근거 문서' 목록 만들기 (유종혁, 11/07). Streamlit 에 의존하지 않는다.

설명
- 답변의 Citation 을 문서별로 묶어 `문서명 · 판본 · PDF n쪽` 한 줄로 만든다. 발췌문은 보여 주지 않는다(개발자 화면 전용).
- 공식 링크는 `data/specs/source_manifest.csv` 의 공식URL 칸에 http(s) 주소가 있는 문서만 붙인다. '확인 필요' 같은 값은 링크로 쓰지 않는다.
- 원본 PDF 파일은 화면에 넣지 않는다. 이용 조건이 확인되지 않은 원본은 공개하지 않는다(프로젝트 계획서의 자료 처리 원칙).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "data" / "specs" / "source_manifest.csv"


def load_urls(path: Path | None = None) -> dict[str, str]:
    """manifest 에서 문서 ID -> 공식 URL. http(s) 로 시작하는 값만 담는다. 파일이 없거나 깨져도 빈 딕셔너리."""
    path = path or MANIFEST
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except (OSError, UnicodeDecodeError, csv.Error):
        return {}
    urls = {}
    for row in rows:
        url = (row.get("공식URL") or "").strip()
        parsed = urlparse(url)
        if row.get("document_id") and parsed.scheme in ("http", "https") and parsed.netloc:
            # 공백·괄호는 인코딩해 마크다운 링크 문법을 깨지 못하게 한다
            urls[row["document_id"]] = quote(url, safe=":/?#[]@!$&'*+,;=%~-._")
    return urls


def group_sources(citations: list[dict], documents: dict) -> list[dict]:
    """Citation 을 문서별로 묶는다. 문서는 처음 나온 순서, 쪽수는 오름차순. 문서 정보가 없으면 문서 ID 를 이름으로 쓴다."""
    groups: dict[str, dict] = {}
    for c in citations:
        g = groups.setdefault(c["document_id"], {"pages": set(), "printed": set()})
        g["pages"].add(c["pdf_page"])
        if c.get("printed_page"):
            g["printed"].add(c["printed_page"])
    result = []
    for doc_id, g in groups.items():
        meta = documents.get(doc_id) or {}
        result.append({"document_id": doc_id, "title": meta.get("title") or doc_id, "version": meta.get("version"),
                       "pages": sorted(g["pages"]), "printed": sorted(g["printed"])})
    return result


def _esc(text: str) -> str:
    """마크다운 문법으로 해석될 수 있는 글자 앞에 역슬래시를 붙인다(문서명·판본 표시용)."""
    return re.sub(r"([\\`*_{}\[\]()<>#|!~])", r"\\\1", str(text))


def source_line(group: dict, url: str | None = None) -> str:
    """한 문서의 근거 한 줄(마크다운). url 이 있으면 문서명에 링크를 건다."""
    title = _esc(group["title"])
    parts = [f"[{title}]({url})" if url else title]
    if group.get("version"):
        parts.append(f"판본 {_esc(group['version'])}")
    pages = "PDF " + ", ".join(f"{p}쪽" for p in group["pages"])
    if group["printed"]:
        pages += " (인쇄 " + ", ".join(f"{p}쪽" for p in group["printed"]) + ")"
    parts.append(pages)
    return " · ".join(parts)


def source_lines(citations: list[dict], documents: dict, urls: dict[str, str]) -> list[str]:
    """답변 하나의 근거 문서 목록(마크다운 줄). 출처가 없으면 빈 목록."""
    return [source_line(g, urls.get(g["document_id"])) for g in group_sources(citations, documents)]


# 사용자에게 보이면 안 되는 내부 낱말(도구 이름·DB 대조 등). 이 낱말이 든 주의 문구는 일반 사용자 화면에서 뺀다.
INTERNAL_WORDS = ("_search", "Finding", "DB", "chunk", "Tool", "도구", "경로", "registry")


def user_warnings(warnings: list[str], limit: int = 5) -> list[str]:
    """일반 사용자에게 보여 줄 주의 문구. 내부 처리 정보가 든 문구는 뺀다. 공백은 하나로 줄인다."""
    cleaned = [re.sub(r"\s+", " ", w).strip() for w in warnings]
    return [w for w in cleaned if w and not any(word in w for word in INTERNAL_WORDS)][:limit]
