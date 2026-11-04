"""B04: TEMP 청크·문서 → 온톨로지 v3.1 6절 공식 ID, 행동 ID 고정 장부.

참고: school_learning_reference.ipynb R04. ref_assign_fixture_action_ids처럼 매 실행 enumerate하지
않는다. 한 번 배정한 action_id는 장부(id_registry.json)에 고정하고 재실행에 재사용한다.

번호 규칙(6절): 블록·표·행동 번호는 같은 쪽 안에서 위→아래 순서.
- 표 번호 t: 표 작업본 Markdown에서 같은 PDF 쪽에 속한 표 블록의 순서(1부터).
  표 작업본이 그 쪽의 모든 표를 옮기지 않았다면 PDF 실제 순서와 다를 수 있으므로 PDF로 확인한다.
- 행 번호 r: 표의 머리글·구분 행을 뺀 데이터 행 순서(1부터).
- 행동 번호 a: 처음 배정할 때 (청크 ID, 행 안의 근거 위치) 순서. 이후 추가되는 행동은 그 쪽의
  기존 최대 번호 다음을 받는다. 번호는 재사용하지 않는다(안정성 > 정렬).
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

from index.school.ids_local import action_id as build_action_id
from index.school.ids_local import chunk_row_id, parse_id
from preprocess.school.markdown_data import annotate_table_blocks, markdown_blocks

REGISTRY_PATH = Path(__file__).resolve().parent / "id_registry.json"

# 02 노트북의 임시 문서 키 → source_manifest.csv의 document_id
TEMP_DOCUMENTS = {"TEMP:school_manual": "moe_school_manual_v3",
                  "TEMP:gyeonggi_guide": "gyeonggi_school_guide_2025"}
# 인쇄 쪽 = PDF 쪽 - 오프셋 (manifest 비고. 쪽 범위 밖이면 PDF로 다시 확인)
PRINTED_OFFSET = {"moe_school_manual_v3": 14, "gyeonggi_school_guide_2025": 10}

TITLE_PREFIX = re.compile(
    r"^#{2,3}\s*PDF\s*\d+(?:\s*[~～-]\s*\d+)?\s*(?:쪽|페이지)\s*"
    r"(?:\(\s*인쇄\s*\d+(?:\s*[~～-]\s*\d+)?\s*쪽\s*\)|·\s*인쇄\s*\d+(?:\s*[~～-]\s*\d+)?\s*쪽)?"
    r"\s*·\s*")


class IdMapError(ValueError):
    """ID 매핑 충돌·장부 불일치."""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 문서

def load_manifest(root: str | Path) -> dict[str, dict]:
    path = Path(root) / "data" / "specs" / "source_manifest.csv"
    with path.open(encoding="utf-8") as handle:
        return {row["document_id"]: row for row in csv.DictReader(handle)}


def official_document_id(temp_document_id: str, manifest: dict[str, dict]) -> str:
    document_id = TEMP_DOCUMENTS.get(temp_document_id)
    if document_id is None:
        raise IdMapError(f"공식 문서 ID 매핑 없음: {temp_document_id}")
    if document_id not in manifest:
        raise IdMapError(f"manifest에 없는 문서: {document_id}")
    return document_id


def document_node(document_id: str, manifest: dict[str, dict]) -> dict:
    """Document 6개 속성만. 확인되지 않은 URL은 null."""
    row = manifest[document_id]
    url = (row.get("공식URL") or "").strip()
    return {"document_id": document_id, "title": row["문서명"], "issuer": row["발행기관"],
            "version": row["판본"], "region": row["적용지역"],
            "source_url": url if url.startswith(("http://", "https://")) else None}


# ---------------------------------------------------------------- 청크

def table_row_position(chunk: dict, raw_text: str) -> tuple[int, int]:
    """표 작업본에서 이 행 청크의 (표 번호, 행 번호)를 원문 위치로 계산한다."""
    row_part = next(p for p in chunk["source_parts"] if p["part"] == "row")
    blocks = annotate_table_blocks(markdown_blocks(raw_text), chunk["source_id"])
    tables = sorted((b for b in blocks if b["kind"] == "table" and b["pdf_page"] == chunk["pdf_page"]),
                    key=lambda b: b["start_offset"])
    for table_no, block in enumerate(tables, 1):
        if not block["start_offset"] <= row_part["start_offset"] < block["end_offset"]:
            continue
        position = block["start_offset"]
        data_rows = []
        for line_no, line in enumerate(block["text"].splitlines(keepends=True)):
            if line_no >= 2 and line.strip():      # 0: 머리글, 1: 구분 행
                data_rows.append(position)
            position += len(line)
        if row_part["start_offset"] not in data_rows:
            raise IdMapError(f"행 시작 위치가 표의 데이터 행과 맞지 않음: {chunk['chunk_id']}")
        return table_no, data_rows.index(row_part["start_offset"]) + 1
    raise IdMapError(f"청크가 속한 표를 그 PDF 쪽에서 찾지 못함: {chunk['chunk_id']}")


def clean_title(title_text: str) -> str:
    """표 작업본 제목에서 'PDF n쪽 · 인쇄 m쪽 ·' 같은 전사 표시를 뗀 표 제목."""
    line = title_text.strip()
    cleaned = TITLE_PREFIX.sub("", line)
    if cleaned == line:
        raise IdMapError(f"표 제목 형식을 알 수 없음: {line[:60]}")
    return cleaned.strip()


def official_chunk(chunk: dict, raw_text: str, manifest: dict[str, dict]) -> dict:
    """TEMP 행 청크 → 공식 Chunk 레코드(적재 속성 5개 + 추적 정보)."""
    document_id = official_document_id(chunk["document_id"], manifest)
    table_no, row_no = table_row_position(chunk, raw_text)
    parts = {p["part"]: p for p in chunk["source_parts"]}
    title = clean_title(parts["title"]["text"])
    offset = PRINTED_OFFSET.get(document_id)
    if chunk["printed_page"] is not None and offset is not None \
            and chunk["printed_page"] != chunk["pdf_page"] - offset:
        raise IdMapError(f"인쇄 쪽 환산 불일치: {chunk['chunk_id']}")
    # Chunk.text: 표 제목 + 머리글 + 행(온톨로지 5-2). Citation 발췌는 행 원문에서 고른다.
    text = f"{title}\n{parts['header']['text']}{parts['row']['text']}".rstrip("\n")
    return {
        "chunk_id": chunk_row_id(document_id, chunk["pdf_page"], table_no, row_no),
        "document_id": document_id, "text": text, "pdf_page": chunk["pdf_page"],
        "printed_page": chunk["printed_page"], "section": title,
        "temp_chunk_id": chunk["chunk_id"], "source_path": chunk["source_path"],
        "content_hash": chunk["content_hash"], "role_label": chunk.get("role_label"),
        "row_text": parts["row"]["text"],
    }


# ---------------------------------------------------------------- 장부

class IdRegistry:
    """청크·행동 ID 고정 장부. save()를 부르기 전에는 파일을 바꾸지 않는다."""

    def __init__(self, data: dict | None = None, path: Path = REGISTRY_PATH):
        self.path = Path(path)
        self.data = deepcopy(data) if data else {"version": 1, "chunks": {}, "actions": []}

    @classmethod
    def load(cls, path: str | Path = REGISTRY_PATH) -> IdRegistry:
        path = Path(path)
        if not path.exists():
            return cls(path=path)
        return cls(json.loads(path.read_text(encoding="utf-8")), path=path)

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path or self.path)
        temporary = target.with_suffix(".tmp")
        body = json.dumps(self.data, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
        temporary.write_text(body, encoding="utf-8")
        temporary.replace(target)
        return target

    # 청크: chunk_id는 원문 위치로 계산되므로 장부는 내용이 바뀌었는지 감지하는 데 쓴다.
    def register_chunk(self, record: dict) -> None:
        entry = {"temp_chunk_id": record["temp_chunk_id"], "row_text_sha256": sha(record["row_text"])}
        known = self.data["chunks"].get(record["chunk_id"])
        if known is None:
            self.data["chunks"][record["chunk_id"]] = entry
        elif known["row_text_sha256"] != entry["row_text_sha256"]:
            raise IdMapError(f"같은 chunk_id의 원문 행이 바뀜(마이그레이션 필요): {record['chunk_id']}")

    def _find_action(self, chunk_id: str, offset: int, evidence: str,
                     split_text: str | None = None) -> dict | None:
        return next((a for a in self.data["actions"] if a["chunk_id"] == chunk_id
                     and a["evidence_offset"] == offset and a["evidence"] == evidence
                     and a.get("split_text") == split_text), None)

    def assign_actions(self, rows: list[dict]) -> tuple[list[dict], list[dict]]:
        """rows(공식 chunk_id가 붙은 정규화 후보)에 action_id를 붙인다. (배정, 보류)를 돌려준다.

        같은 원문 근거에서 나눈 행동(예: 보호자 연락 / 진료 요청 분리)은 LLM 원래 행동 문장
        (split_text)으로 구별해 각자 ID를 받는다. 원래 문장까지 같으면 진짜 중복이므로 보류한다.
        """
        anchors: dict[tuple, list[dict]] = {}
        for row in rows:
            anchors.setdefault((row["chunk_id"], row["evidence_offset"], row["evidence"]), []).append(row)
        assigned, held = [], []
        new = []
        for anchor, group in anchors.items():
            texts = [row["raw"]["action_text"] for row in group]
            if len(set(texts)) != len(texts):
                for row in group:
                    held.append({**deepcopy(row), "hold_reasons":
                                 ["같은 원문 근거·같은 행동 문장의 후보가 여러 개 - 중복"]})
                continue
            for row in group:
                split_text = row["raw"]["action_text"] if len(group) > 1 else None
                known = self._find_action(*anchor, split_text)
                if known is None:
                    new.append({**row, "split_text": split_text})
                else:
                    assigned.append({**deepcopy(row), "action_id": known["action_id"],
                                     "action_id_status": "REUSED"})
        used: dict[tuple, int] = {}
        for entry in self.data["actions"]:
            parsed = parse_id(entry["action_id"])
            key = (parsed["document_id"], parsed["page"])
            used[key] = max(used.get(key, 0), parsed["action"])
        for row in sorted(new, key=lambda r: (r["chunk_id"], r["evidence_offset"], r["split_text"] or "")):
            key = (row["document_id"], row["pdf_page"])
            used[key] = used.get(key, 0) + 1
            action = build_action_id(row["document_id"], row["pdf_page"], used[key])
            self.data["actions"].append({
                "action_id": action, "chunk_id": row["chunk_id"],
                "evidence_offset": row["evidence_offset"], "evidence": row["evidence"],
                "split_text": row["split_text"], "action_text": row["action_text"],
                "candidate_key": row["candidate_key"], "registered_from_run": row["run_dir"]})
            assigned.append({**deepcopy(row), "action_id": action, "action_id_status": "NEW"})
        return sorted(assigned, key=lambda r: r["action_id"]), held


def attach_official_ids(normalized: list[dict], chunks: list[dict], raw_texts: dict[str, str],
                        manifest: dict[str, dict], registry: IdRegistry
                        ) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """정규화 후보에 공식 document_id·chunk_id·action_id를 붙인다.

    반환: (ID 배정된 행동, 보류, {TEMP chunk_id: 공식 Chunk 레코드})
    공식 청크 목록은 행동이 없는 검증된 청크(예: 아직 추출하지 않은 보건교사 행)도 포함한다.
    """
    official = {}
    for chunk in chunks:
        record = official_chunk(chunk, raw_texts[chunk["source_path"]], manifest)
        registry.register_chunk(record)
        official[chunk["chunk_id"]] = record
    rows = []
    for row in normalized:
        record = official[row["source_chunk_id"]]
        rows.append({**deepcopy(row), "temp_document_id": row["document_id"],
                     "document_id": record["document_id"], "chunk_id": record["chunk_id"],
                     "pdf_page": record["pdf_page"]})
    assigned, held = registry.assign_actions(rows)
    return assigned, held, official
