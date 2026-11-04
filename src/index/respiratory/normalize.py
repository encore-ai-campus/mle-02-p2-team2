"""M5 정규화: 사람이 기록한 검토 결정과 원문을 대조한다.

실행: 저장소 루트에서 uv run python -m index.respiratory.normalize
입력: steps_raw.jsonl과 blocks.jsonl
출력: steps_normalized.jsonl. 입력 행의 순서와 원문 문자열은 유지한다.

검토 결정이 없는 행동이나 원문·스키마 검사에 문제가 있는 행동은 PENDING이다.
검토 결정과 검사를 모두 통과한 행동만 APPROVED로 표시한다.
"""

import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

from index.respiratory.extractor import (
    ALLOWED_BASES,
    ALLOWED_CONCEPT_IDS,
    ALLOWED_DISEASE_IDS,
    ALLOWED_MODALITIES,
    ALLOWED_ROLE_IDS,
    ALLOWED_SCOPES,
    ALLOWED_STAGES,
    read_jsonl,
    validate_action,
)
from index.school.ids_local import IdError, parse_id

# 사용자가 원문을 대조한 뒤 지정한 결정이다.
# 없는 action_id를 추측해 승인하지 않으며 새 결정은 이 표에 명시한다.
REVIEW_DECISIONS = {
    "kdca_respiratory_2026:p0116:a01": "APPROVE",
    "kdca_flu_facility_2026:p0015:a01": {"exclude": "목적·기본방향 항목(규칙②)"},
    "kdca_flu_facility_2026:p0015:a02": {"exclude": "목적·기본방향 항목(규칙②)"},
    "kdca_flu_facility_2026:p0015:a03": {"exclude": "목적·기본방향 항목(규칙②)"},
    "kdca_flu_facility_2026:p0015:a04": {"exclude": "목적·기본방향 항목(규칙②)"},
    "kdca_flu_facility_2026:p0015:a05": {"exclude": "목적·기본방향 항목(규칙②)"},
    "kdca_flu_facility_2026:p0016:a01": {"exclude": "목적·기본방향 항목(규칙②)"},
    "kdca_flu_facility_2026:p0016:a02": {"exclude": "목적·기본방향 항목(규칙②)"},
    "kdca_flu_facility_2026:p0016:a03": "APPROVE",
    "kdca_flu_facility_2026:p0017:a01": "APPROVE",
    "kdca_flu_facility_2026:p0017:a02": "APPROVE",
    "kdca_flu_facility_2026:p0017:a03": "APPROVE",
    "kdca_flu_facility_2026:p0017:a04": {"set": {"instance_of": "CONTACT_GUARDIAN"}},
    "kdca_flu_facility_2026:p0017:a05": "APPROVE",
    "kdca_flu_facility_2026:p0017:a06": "APPROVE",
    "kdca_flu_facility_2026:p0018:a01": "APPROVE",
    "kdca_flu_facility_2026:p0018:a02": "APPROVE",
    "kdca_flu_facility_2026:p0018:a03": {"set": {"instance_of": "VENTILATE_DISINFECT"}},
    "kdca_flu_facility_2026:p0018:a04": "APPROVE",
    "kdca_flu_facility_2026:p0018:a05": "APPROVE",
    "kdca_flu_facility_2026:p0018:a06": {"set": {"instance_of": "STAFF_DUTY_SUBSTITUTE"}},
}

# 허용 목록은 추출기의 같은 객체를 참조해 두 파일의 기준이 갈라지지 않게 한다.
EXTRACTOR_ALLOWED = {
    "scope": ALLOWED_SCOPES,
    "stage": ALLOWED_STAGES,
    "modality": ALLOWED_MODALITIES,
    "basis": ALLOWED_BASES,
    "disease": ALLOWED_DISEASE_IDS,
    "role": ALLOWED_ROLE_IDS,
    "concept": ALLOWED_CONCEPT_IDS,
}

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INTERIM_DIR = PROJECT_ROOT / "data/interim/respiratory"
RAW_PATH = INTERIM_DIR / "steps_raw.jsonl"
BLOCKS_PATH = INTERIM_DIR / "blocks.jsonl"
OUTPUT_PATH = INTERIM_DIR / "steps_normalized.jsonl"


def index_blocks(records):
    """청크 ID로 원문을 찾되 중복 ID는 조용히 덮어쓰지 않는다."""
    chunks = {}
    for _, chunk in records:
        chunk_id = chunk.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise ValueError("blocks.jsonl에 chunk_id가 없는 행이 있습니다.")
        if chunk_id in chunks:
            raise ValueError(f"blocks.jsonl에 중복된 chunk_id가 있습니다: {chunk_id}")
        chunks[chunk_id] = chunk
    return chunks


def parse_expected_id(value, expected_kinds, label, issues):
    """공통 ID 검사기를 사용하고 오류는 해당 행동의 검수 사유에 남긴다."""
    try:
        parsed = parse_id(value)
    except IdError:
        issues.append(f"{label}: ID 형식이 올바르지 않습니다: {value!r}")
        return None
    if parsed["kind"] not in expected_kinds:
        issues.append(f"{label}: ID 종류가 올바르지 않습니다: {value!r}")
        return None
    # 공통 파서는 형식과 쪽을 검사한다. 번호 00은 전역 ID 규약상 사용할 수 없다.
    number_fields = ("action",) if parsed["kind"] == "action" else ("block", "table", "row")
    if any(parsed.get(field, 1) < 1 for field in number_fields):
        issues.append(f"{label}: ID 번호는 1부터 시작해야 합니다: {value!r}")
        return None
    return parsed


def normalize_row(source, chunks):
    """검토 결정을 반영한 복사본과 검수 결과를 반환한다."""
    row = deepcopy(source)
    action_id = row.get("action_id")
    decision = REVIEW_DECISIONS.get(action_id)
    decision_note = ""
    if isinstance(decision, dict) and "set" in decision:
        # 사람이 명시한 개념 ID만 바꾼다. 행동 문구·인용문은 손대지 않는다.
        replacement = decision["set"]["instance_of"]
        if replacement not in EXTRACTOR_ALLOWED["concept"]:
            raise ValueError(f"{action_id}: 검토 결정의 개념 ID가 허용 목록에 없습니다.")
        row["instance_of"] = replacement
        decision_note = f"instance_of를 {replacement}로 변경 후 승인 검토"
    elif isinstance(decision, dict) and "exclude" in decision:
        decision_note = decision["exclude"]

    issues = []
    action_parts = parse_expected_id(action_id, {"action"}, "action_id", issues)
    supported = row.get("supported_by")
    chunk_id = supported[0] if isinstance(supported, list) and supported else None
    chunk_parts = parse_expected_id(chunk_id, {"chunk_block", "chunk_row"}, "chunk_id", issues)
    chunk = chunks.get(chunk_id) if isinstance(chunk_id, str) else None
    if chunk is None:
        issues.append(f"근거 청크가 없습니다: {chunk_id!r}")
    elif not isinstance(chunk.get("text"), str):
        issues.append(f"근거 청크의 text가 문자열이 아닙니다: {chunk_id}")
    # 근거가 없어도 형식·허용값 검사는 실행한다. 원문이 없다는 사유도 위에 남긴다.
    source_text = chunk.get("text", "") if isinstance(chunk, dict) else ""
    if not isinstance(source_text, str):
        source_text = ""
    issues.extend(validate_action(row, row.get("chunk_diseases"), source_text))

    if action_parts and chunk_parts and (
        action_parts["document_id"], action_parts["page"]
    ) != (chunk_parts["document_id"], chunk_parts["page"]):
        issues.append("action_id의 문서·PDF 쪽이 근거 chunk_id와 다릅니다.")
    if chunk is not None and chunk_parts:
        if chunk.get("document_id") != chunk_parts["document_id"]:
            issues.append("청크의 document_id가 chunk_id와 다릅니다.")
        if chunk.get("pdf_page") != chunk_parts["page"]:
            issues.append("청크의 pdf_page가 chunk_id와 다릅니다.")

    diseases = row.get("for_disease")
    if row.get("scope") == "NAMED_DISEASES":
        if not isinstance(diseases, list) or not diseases:
            issues.append("NAMED_DISEASES에는 for_disease가 하나 이상 필요합니다.")
    elif diseases != []:
        issues.append("NAMED_DISEASES가 아니면 for_disease는 빈 배열이어야 합니다.")
    if row.get("instance_of") == "NEW_CONCEPT_CANDIDATE":
        issues.append("NEW_CONCEPT_CANDIDATE는 확정된 개념이 아니어서 승인할 수 없습니다.")
    if decision is None:
        issues.append("검토 결정이 없어 승인할 수 없습니다.")

    # 원래 검수 메타데이터(모델·프롬프트 등)를 보존하고 정규화 결과만 덧붙인다.
    original_review = row.get("review")
    review = deepcopy(original_review) if isinstance(original_review, dict) else {}
    if not isinstance(original_review, dict):
        issues.append("기존 review가 객체가 아닙니다.")
    if isinstance(decision, dict) and "exclude" in decision:
        status = "EXCLUDED"
    elif decision is not None and not issues:
        status = "APPROVED"
    else:
        status = "PENDING"
    review.update({
        "status": status,
        "issues": list(dict.fromkeys(issues)),
        "decision": deepcopy(decision),
        "decision_note": decision_note,
        "normalize_version": "n1",
    })
    row["review"] = review
    return row


def main():
    """입력 순서대로 정규화해 저장하고 상태별 건수와 사유를 출력한다."""
    chunks = index_blocks(read_jsonl(BLOCKS_PATH))
    records = read_jsonl(RAW_PATH)
    rows = [normalize_row(row, chunks) for _, row in records]
    with OUTPUT_PATH.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts = Counter(row["review"]["status"] for row in rows)
    for status in ("APPROVED", "PENDING", "EXCLUDED"):
        print(f"{status}: {counts[status]}건")
    for row in rows:
        review = row["review"]
        if review["status"] in {"PENDING", "EXCLUDED"}:
            reasons = list(review["issues"])
            if review["decision_note"]:
                reasons.insert(0, review["decision_note"])
            print(f"{review['status']} {row.get('action_id')}: {'; '.join(reasons)}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError) as error:
        print(f"오류: {error}", file=sys.stderr)
        sys.exit(1)
