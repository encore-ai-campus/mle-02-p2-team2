"""B01~B03: 실행 산출물 로더·승인 후보 연결, 청크·원문 근거 검증, 역할·질병·개념 매퍼.

참고: school_learning_reference.ipynb R01(shape_ok·clean_rows·kept), R02(find_row·check),
R03(normalize·rule_link). 수업의 회사 전용 정리 규칙과 '확인 불가도 일부 적재' 정책은 쓰지 않는다.

원칙
- 원본 후보는 지우지 않는다. 모든 후보는 ready 또는 held 중 하나로 가며 raw를 그대로 품는다.
- 자동 문자열 검사(B02)는 사람 검수(review.jsonl)를 대신하지 않고, 사람 검수도 자동 검사를
  건너뛰게 하지 않는다. 둘 다 통과해야 ready다.
- 비교용 정규화(compare_key)는 연결할 때만 쓰고 원문 표기는 보존한다.
- NEW_CONCEPT_CANDIDATE는 표준 concept_id가 아니다. 개념 연결 여부는 B05 정책으로 정한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

from index.school.dictionaries import alias_index, compare_key

SCOPES = {"COMMON", "DISEASE_GROUP", "NAMED_DISEASES", "UNKNOWN"}
STAGES = {"PREVENTION", "RESPONSE_1", "RESPONSE_2", "RESPONSE_3", "RECOVERY", "ALL_STAGES",
          "NATIONAL_CRISIS", "UNSPECIFIED"}
MODALITIES = {"MANDATORY", "RECOMMENDED", "DISCRETIONARY", "UNSPECIFIED"}
BASES = {"EXPLICIT", "EXAMPLE"}
NEW_CONCEPT = "NEW_CONCEPT_CANDIDATE"

# 검수 화면(review_ui)의 5개 체크 항목. 모두 True여야 승인으로 본다.
REVIEW_FLAGS = ("pdf_source_checked", "role_row_checked", "example_basis_checked",
                "stage_condition_checked", "concept_checked")

# 02 노트북 check_candidate가 남기는 사유 → 그 사유를 해소하는 사람 검수 항목.
# 여기에 없는 사유가 나오면 해소 근거를 알 수 없으므로 보류한다.
CHECK_REASON_FLAGS = {
    "예시 단서와 역할 배정의 관계를 PDF에서 확인 필요": "example_basis_checked",
    "역할 근거 원문 확인 필요": "role_row_checked",
    "범위와 직접 명시된 질병 후보 확인 필요": "stage_condition_checked",
    "행동 근거가 원문 한 조각에 연속으로 존재하지 않음": "pdf_source_checked",
}


class ArtifactError(ValueError):
    """실행 폴더 자체를 쓸 수 없는 오류(파일 없음, 표준 JSON 아님, 실행 실패 등)."""


# ---------------------------------------------------------------- B01 로더

def _reject_constant(constant):
    raise ArtifactError(f"표준 JSON에 없는 값: {constant}")


def read_jsonl(path: Path) -> list[dict]:
    """NaN·Infinity를 거부하는 엄격한 JSONL 읽기."""
    if not path.is_file():
        raise ArtifactError(f"파일 없음: {path}")
    rows = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line, parse_constant=_reject_constant))
                except json.JSONDecodeError as exc:
                    raise ArtifactError(f"{path.name}:{number} JSON 오류: {exc.msg}") from exc
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    """표준 JSON만 쓰고(allow_nan=False) 다시 엄격하게 읽어 건수를 확인한다. 덮어쓰지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    if len(read_jsonl(path)) != len(rows):
        raise ArtifactError(f"재읽기 건수 불일치: {path}")
    return path


def candidate_key(candidate: dict) -> str:
    """검수 화면(src/preprocess/school/review_ui.py candidate_key)과 같은 후보 지문."""
    body = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


def load_artifacts(run_dir: str | Path) -> dict:
    """명시한 실행 폴더 하나의 청크·후보·검수·실행 기록을 읽는다. '최신 폴더'를 추측하지 않는다."""
    run_dir = Path(run_dir)
    runs = read_jsonl(run_dir / "run.jsonl")
    if not runs:
        raise ArtifactError("run.jsonl이 비어 있음")
    succeeded = {r.get("source_chunk_id") for r in runs if r.get("status") == "SUCCESS"}
    if not succeeded:
        raise ArtifactError(f"성공하지 않은 실행 기록: {[r.get('status') for r in runs]}")
    chunks = read_jsonl(run_dir / "chunks.jsonl")
    ids = [c["chunk_id"] for c in chunks]
    if len(ids) != len(set(ids)):
        raise ArtifactError("chunks.jsonl에 중복 chunk_id")
    candidates = read_jsonl(run_dir / "steps_raw.jsonl")
    # 일부 청크 호출이 실패해도 성공한 청크의 후보는 쓴다. 실패 청크에서 온 후보가 있으면 이상하다.
    stray = {c.get("source_chunk_id") for c in candidates} - succeeded
    if stray:
        raise ArtifactError(f"성공 기록이 없는 청크의 후보: {sorted(stray)}")
    return {"run_dir": run_dir.name, "runs": runs, "chunks": chunks, "candidates": candidates,
            "failed_runs": [r for r in runs if r.get("status") != "SUCCESS"],
            "reviews": read_jsonl(run_dir / "review.jsonl")}


def load_runs(run_dirs: list[str | Path]) -> tuple[list[dict], list[dict]]:
    """여러 실행 폴더를 읽는다. 같은 TEMP chunk_id는 내용이 완전히 같아야 하나로 합친다.

    반환: (폴더별 artifacts 목록, 합친 청크 목록)
    """
    loaded = [load_artifacts(path) for path in run_dirs]
    if len({a["run_dir"] for a in loaded}) != len(loaded):
        raise ArtifactError("같은 실행 폴더가 두 번 들어옴")
    chunks: dict[str, dict] = {}
    for artifacts in loaded:
        for chunk in artifacts["chunks"]:
            known = chunks.setdefault(chunk["chunk_id"], chunk)
            if known != chunk:
                raise ArtifactError(f"실행 폴더마다 내용이 다른 청크: {chunk['chunk_id']}")
    return loaded, list(chunks.values())


def select_approved_runs(loaded: list[dict]) -> tuple[list[dict], list[dict]]:
    """폴더마다 select_approved를 적용해 합친다(검수 기록은 자기 폴더의 후보와만 연결)."""
    ready, held = [], []
    for artifacts in loaded:
        r, h = select_approved(artifacts)
        ready += r
        held += h
    return ready, held


def shape_reasons(candidate: dict) -> list[str]:
    """R01 shape_ok의 학교 버전: 필수 문자열·허용값·순서·역할 basis."""
    reasons = []
    for key in ("document_id", "source_chunk_id", "action_text", "evidence"):
        value = candidate.get(key)
        if not isinstance(value, str) or not value.strip():
            reasons.append(f"필수 문자열 누락: {key}")
    for key, allowed in (("scope", SCOPES), ("stage", STAGES), ("modality", MODALITIES)):
        if candidate.get(key) not in allowed:
            reasons.append(f"허용값 위반: {key}={candidate.get(key)!r}")
    order = candidate.get("step_order")
    if order is not None and (type(order) is not int or order < 1):
        reasons.append(f"step_order 형식 위반: {order!r}")
    for key in ("when_text", "timing_text"):
        value = candidate.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            reasons.append(f"{key}는 문자열 또는 null")
    roles = candidate.get("roles")
    if not isinstance(roles, list):
        reasons.append("roles는 목록")
    else:
        for role in roles:
            if not isinstance(role, dict) or not str(role.get("role_text") or "").strip():
                reasons.append("역할 표기 누락")
            elif role.get("basis") not in BASES:
                reasons.append(f"역할 basis 누락·허용값 위반: {role.get('role_text')}")
    if not isinstance(candidate.get("named_diseases"), list):
        reasons.append("named_diseases는 목록")
    return reasons


def review_reasons(candidate: dict, review: dict | None) -> list[str]:
    """사람 검수 결과로 승인 여부를 판단한다."""
    if review is None:
        return ["검수 기록 없음"]
    reasons = []
    if review.get("review_status") != "APPROVED":
        reasons.append(f"미승인: {review.get('review_status')}")
    if not str(review.get("reviewer") or "").strip() or not review.get("reviewed_at"):
        reasons.append("검수자·검수 시각 누락")
    missing = [flag for flag in REVIEW_FLAGS if review.get(flag) is not True]
    if missing:
        reasons.append(f"검수 항목 미완료: {missing}")
    for reason in candidate.get("check_reasons") or []:
        flag = CHECK_REASON_FLAGS.get(reason)
        if flag is None:
            reasons.append(f"해소 근거를 알 수 없는 자동 확인 사유: {reason}")
        elif review.get(flag) is not True:
            reasons.append(f"자동 확인 사유 미해소: {reason}")
    if review.get("evidence_location") not in (None, candidate.get("source_chunk_id")):
        reasons.append("검수 근거 위치가 후보 청크와 다름")
    return reasons


def select_approved(artifacts: dict) -> tuple[list[dict], list[dict]]:
    """후보를 지문으로 검수 결과와 연결해 ready/held로 나눈다(R01 clean_rows/kept 패턴).

    검수 행 번호·후보 순서만으로 연결하지 않는다. 같은 지문의 검수 기록이 둘 이상이면 보류한다.
    """
    by_key: dict[str, list[dict]] = {}
    for review in artifacts["reviews"]:
        by_key.setdefault(review.get("candidate_fingerprint") or "", []).append(review)
    ready, held = [], []
    for index, raw in enumerate(artifacts["candidates"], 1):
        key = candidate_key(raw)
        matches = by_key.get(key, [])
        reasons = shape_reasons(raw)
        review = matches[0] if len(matches) == 1 else None
        if len(matches) > 1:
            reasons.append("같은 후보의 검수 기록이 여러 개")
        else:
            reasons += review_reasons(raw, review)
        edited = (review or {}).get("edited_action_text")
        row = {
            "candidate_key": key, "candidate_index": index, "run_dir": artifacts["run_dir"],
            "raw": deepcopy(raw), "review": deepcopy(review),
            "document_id": raw.get("document_id"), "source_chunk_id": raw.get("source_chunk_id"),
            # 사람이 고친 행동 문장이 있으면 그것을 쓰고, 원래 문장은 raw에 남는다.
            "action_text": edited.strip() if isinstance(edited, str) and edited.strip()
            else raw.get("action_text"),
            "action_text_edited": bool(isinstance(edited, str) and edited.strip()),
            # 검수자가 고른 개념(있으면 LLM 후보 대신 사용). 표준 사전에 있어야 연결된다.
            "concept_override": (review or {}).get("edited_concept_id") or None,
            "hold_reasons": reasons,
        }
        (held if reasons else ready).append(row)
    known = {r["candidate_key"] for r in ready + held}
    orphans = [r for r in artifacts["reviews"] if r.get("candidate_fingerprint") not in known]
    if orphans:
        # 후보가 바뀌어 지문이 달라진 검수 기록. 조용히 버리지 않고 알린다.
        missing = sum(not r.get("candidate_fingerprint") for r in orphans)
        raise ArtifactError(f"후보와 연결되지 않는 검수 기록 {len(orphans)}건 (지문 없음 {missing}건) - "
                            "후보가 검수 뒤 바뀌었거나 지문 없이 저장된 기록. 검수 화면에서 다시 저장")
    return ready, held


# ---------------------------------------------------------------- B02 원문 근거

def load_raw_texts(root: str | Path, chunks: list[dict]) -> dict[str, str]:
    """청크가 가리키는 변환본 Markdown을 읽는다(01 노트북과 같은 utf-8-sig)."""
    root = Path(root)
    return {path: (root / path).read_text(encoding="utf-8-sig")
            for path in sorted({c["source_path"] for c in chunks})}


def chunk_reasons(chunk: dict, raw_texts: dict[str, str]) -> list[str]:
    """청크가 원문 파일의 기록된 위치와 정확히 같은지, PDF 대조가 끝났는지 확인한다."""
    reasons = []
    text = raw_texts.get(chunk.get("source_path"))
    if text is None:
        return ["원문 파일 없음"]
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != chunk.get("content_hash"):
        reasons.append("원문 파일 해시가 추출 당시와 다름")
    if chunk.get("page_verification") != "VERIFIED_PDF":
        reasons.append(f"PDF 쪽 대조 미완료: {chunk.get('page_verification')}")
    page = chunk.get("pdf_page")
    if type(page) is not int or page < 1:
        reasons.append("pdf_page는 1 이상 정수")
    printed = chunk.get("printed_page")
    if printed is not None and (type(printed) is not int or printed < 1):
        reasons.append("printed_page는 1 이상 정수 또는 null")
    parts = chunk.get("source_parts") or []
    if [p.get("part") for p in parts] != ["title", "header", "row"]:
        reasons.append("표 행 청크 구성(title·header·row)이 아님")
    for part in parts:
        if text[part["start_offset"]:part["end_offset"]] != part["text"]:
            reasons.append(f"원문 위치 불일치: {part['part']}")
    if chunk.get("text") != "".join(p["text"] for p in parts):
        reasons.append("청크 text가 원문 조각의 연결과 다름")
    return reasons


def _row_part(chunk: dict) -> dict | None:
    return next((p for p in chunk.get("source_parts") or [] if p.get("part") == "row"), None)


_BR = re.compile(r"<br\s*/?>")
_IGNORED = set(" \t\r\n|-–∙•‣¢·;:,.()[]<>「」『』※‘’“”'\"･")


def _loose(text: str) -> list[tuple[str, int]]:
    """(글자, 원문 위치) 목록. <br>·공백·표 구분자·글머리표·문장부호를 뺀 비교용(원문은 고치지 않는다)."""
    skip = set()
    for match in _BR.finditer(text):
        skip |= set(range(match.start(), match.end()))
    return [(ch.casefold(), i) for i, ch in enumerate(text) if i not in skip and ch not in _IGNORED]


def loose_find(haystack: str, needle: str) -> int:
    """needle이 haystack에 (줄바꿈·<br>·공백·글머리표 차이를 무시하고) 연속으로 있으면 원문 시작 위치, 없으면 -1."""
    wanted = "".join(ch for ch, _ in _loose(needle))
    if not wanted:
        return -1
    kept = _loose(haystack)
    position = "".join(ch for ch, _ in kept).find(wanted)
    return kept[position][1] if position >= 0 else -1


def validate_source(candidate: dict, chunks_by_id: dict[str, dict],
                    chunk_problems: dict[str, list[str]]) -> list[str]:
    """후보의 문서·청크 소속과 인용을 원문 조각에서 확인한다(R02 check의 학교 버전).

    - 행동 근거(evidence)는 표 **행** 조각 안에서만 찾는다(<br>·공백·글머리표 차이는 무시).
    - 역할 근거(basis_evidence)는 제목·머리글·행 중 한 조각에서 찾는다. 예시 단서가 표 제목('주요 역할(예시)')에 있는
      역할표가 있기 때문이다. 단, 모델이 역할을 행의 담당 칸 대신 다른 곳에서 인용했더라도 role_text가 행 담당과
      같으면 행 구조가 근거이므로 받아들인다. 편집 메모에만 있는 문장은 인정하지 않는다.
    - 제목·머리글과 이어 붙인 문장은 원문의 연속 인용으로 인정하지 않는다.
    """
    raw = candidate.get("raw", candidate)   # select_approved 결과든 원래 후보든 받는다
    chunk = chunks_by_id.get(raw.get("source_chunk_id"))
    if chunk is None:
        return ["청크 없음"]
    reasons = [f"청크 문제: {r}" for r in chunk_problems.get(chunk["chunk_id"], [])]
    if chunk["document_id"] != raw.get("document_id"):
        reasons.append("청크의 문서 소속 불일치")
    row = _row_part(chunk)
    row_text = row["text"] if row else ""
    evidence = raw.get("evidence") or ""
    # 편집 메모(editorial_context)·제목·머리글에만 있는 문장은 여기서 자동으로 걸러진다.
    if not evidence.strip() or (evidence not in row_text and loose_find(row_text, evidence) < 0):
        reasons.append("행동 근거가 해당 표 행 원문에 연속으로 없음")
    parts_text = [p["text"] for p in chunk.get("source_parts") or []]
    row_label = compare_key(chunk.get("role_label") or "")
    for role in raw.get("roles") or []:
        basis_evidence = role.get("basis_evidence") or ""
        found = bool(basis_evidence.strip()) and any(
            basis_evidence in text or loose_find(text, basis_evidence) >= 0 for text in parts_text)
        same_as_row = bool(row_label) and compare_key(role.get("role_text") or "") == row_label
        if not (found or same_as_row):
            reasons.append(f"역할 근거가 원문 조각(제목·머리글·행)에 없음: {role.get('role_text')}")
    return reasons


def evidence_offset(candidate: dict, chunk: dict) -> int:
    """행 조각 안에서 근거가 처음 나오는 위치(원문 위→아래 순서 기준)."""
    row = _row_part(chunk)
    position = row["text"].find(candidate["evidence"]) if row else -1
    if position < 0 and row:
        position = loose_find(row["text"], candidate["evidence"])
    if position < 0:
        raise ValueError("근거 위치를 찾을 수 없음")
    return position


# ---------------------------------------------------------------- B03 매퍼

def resolve_mention(mention: str, book: dict, id_field: str) -> dict:
    """표기 하나를 표준 ID로 연결한다(R03 rule_link의 학교 버전).

    정확 표기·별칭의 유일한 일치만 RESOLVED. 복합 표기, 학교 밖 주체, 미등록 표기,
    검토용 별칭은 ID 없이 사유와 함께 돌려준다.
    """
    key = compare_key(mention or "")
    result = {"mention": mention, "id": None, "status": "UNRESOLVED", "method": None, "reason": ""}
    if not key:
        return {**result, "reason": "빈 표기"}
    for item in book.get("compound_mentions", []):
        if compare_key(item["mention"]) == key:
            return {**result, "status": "COMPOUND", "candidates": list(item["role_ids"]),
                    "reason": "한 칸에 여러 역할 - 사람 검수 필요"}
    for item in book.get("excluded_mentions", []):
        if compare_key(item["mention"]) == key:
            return {**result, "status": "EXCLUDED", "reason": item["reason"]}
    for item in book.get("unregistered_mentions", []):
        if compare_key(item["mention"]) == key:
            return {**result, "status": "UNREGISTERED", "reason": "사전 미등록 역할(승인 필요)"}
    index = alias_index(book["entries"], id_field)
    if key in index:
        names = {compare_key(e["name"]): e[id_field] for e in book["entries"]}
        return {**result, "id": index[key], "status": "RESOLVED",
                "method": "NAME" if key in names else "ALIAS"}
    for entry in book["entries"]:
        if key in {compare_key(a) for a in entry.get("review_aliases", [])}:
            return {**result, "status": "REVIEW_ALIAS", "candidates": [entry[id_field]],
                    "reason": "검토용 별칭 - 자동 연결 안 함"}
    return {**result, "reason": "사전에 없음"}


def resolve_concept(candidate_concept: str | None, book: dict) -> dict:
    """개념 후보를 표준 concept_id로 확인한다. 유사도로 새 개념을 고르지 않는다.

    승인 대기 목록(pending_candidates)의 제안 ID는 PENDING_CONCEPT - 3인 승인 후 entries로
    옮기면 같은 입력이 RESOLVED가 된다.
    """
    ids = {e["concept_id"] for e in book["entries"]}
    pending = {p["proposed_id"] for p in book.get("pending_candidates", []) if p.get("proposed_id")}
    if candidate_concept in ids:
        return {"id": candidate_concept, "status": "RESOLVED", "reason": ""}
    if candidate_concept in pending:
        return {"id": None, "status": "PENDING_CONCEPT", "proposed_id": candidate_concept,
                "reason": f"승인 대기 개념: {candidate_concept}"}
    if candidate_concept == NEW_CONCEPT:
        return {"id": None, "status": "NEW_CONCEPT", "reason": "표준 사전에 없는 새 개념 후보"}
    if candidate_concept is None:
        return {"id": None, "status": "NONE", "reason": "개념 후보 없음"}
    return {"id": None, "status": "UNREGISTERED", "reason": f"사전 밖 개념 ID: {candidate_concept}"}


def map_candidate(candidate: dict, chunk: dict, books: dict) -> tuple[dict, list[str]]:
    """역할·질병·개념을 연결한다. 실패 사유(보류)와 연결 결과를 함께 돌려준다.

    DONE_BY는 roles 목록(수행자)에서만 만든다. 행동 문장 속 수신자(예: '보건교사에게 전달')는
    수행 역할로 연결하지 않는다.
    """
    raw = candidate["raw"]
    reasons: list[str] = []
    roles, dropped, role_reasons = _performer_roles(candidate, chunk, books)
    reasons += role_reasons

    diseases = []
    for mention in raw.get("named_diseases") or []:
        mapped = resolve_mention(mention, books["disease"], "disease_id")
        if mapped["status"] != "RESOLVED":
            reasons.append(f"질병 연결 실패({mapped['status']}): {mention} - {mapped['reason']}")
        else:
            diseases.append(mapped["id"])
    if raw.get("scope") == "NAMED_DISEASES" and not diseases:
        reasons.append("NAMED_DISEASES인데 연결된 질병 없음")
    if raw.get("scope") != "NAMED_DISEASES" and raw.get("named_diseases"):
        reasons.append("특정 질병 범위가 아닌데 질병이 있음(질병군에서 질병 추론 금지)")

    override = candidate.get("concept_override")
    concept = resolve_concept(override or raw.get("concept_candidate"), books["concept"])
    concept["source"] = "REVIEW" if override else "LLM"
    if concept["status"] == "UNREGISTERED":
        reasons.append(concept["reason"])
    return {"roles": roles, "dropped_role_mentions": dropped, "disease_ids": sorted(set(diseases)),
            "concept": concept}, reasons


def _performer_roles(candidate: dict, chunk: dict, books: dict) -> tuple[list, list, list]:
    """역할을 정하고, 예시 역할표 문서의 표 행이면 basis를 EXAMPLE로 맞춘다(아래 _decide_roles 참고).

    온톨로지 v3.1 5-1: 교육부 매뉴얼·경기도 가이드북의 역할표 연결은 기본 EXAMPLE이다. 두 문서의
    시나리오 표에는 '예시이며 학교 상황에 따라 정할 수 있다'는 단서가 붙어 있으므로, LLM이 EXPLICIT을
    냈어도 표 행 청크에서는 EXAMPLE로 고치고 basis_corrected에 남긴다.
    """
    roles, dropped, reasons = _decide_roles(candidate, chunk, books)
    if chunk.get("role_label") and chunk.get("document_id") in EXAMPLE_ROLE_TABLE_DOCUMENTS:
        for role in roles:
            if role["basis"] != "EXAMPLE":
                role["basis_corrected"] = f"{role['basis']}→EXAMPLE (예시 역할표)"
                role["basis"] = "EXAMPLE"
    return roles, dropped, reasons


# 역할표가 '예시'로 제시된 문서(02 노트북 TEMP 문서 키). 공식 ID로는 moe_school_manual_v3, gyeonggi_school_guide_2025.
EXAMPLE_ROLE_TABLE_DOCUMENTS = {"TEMP:school_manual", "TEMP:gyeonggi_guide"}


def _decide_roles(candidate: dict, chunk: dict, books: dict) -> tuple[list, list, list]:
    """수행 역할(DONE_BY)을 정한다. 반환: (수행 역할, 수행자로 보지 않은 표기, 보류 사유).

    역할표 행 청크에서는 **그 행의 담당 칸이 수행자**다(표 구조가 근거).
    - LLM 역할에 행 담당이 있으면 행 담당만 쓰고, 나머지(요청 받는 교무부장·담임 등)는 수신자로 뺀다.
    - LLM 역할이 모두 사전에 없는 표기(예: '담당 교사')이고 검수자가 '같은 행의 역할'을 확인했으면
      행 담당을 쓴다.
    - 행 담당이 아닌 다른 학교 역할만 수행자로 나오면 행동 문장 자체가 행과 어긋난 것이므로 보류한다.
    행 담당이 없는 청크(본문 문단)에서는 LLM 역할을 사전으로만 연결한다.
    """
    raw = candidate["raw"]
    mentions = raw.get("roles") or []
    row = resolve_mention(chunk.get("role_label") or "", books["role"], "role_id")
    mapped = [(m, resolve_mention(m["role_text"], books["role"], "role_id")) for m in mentions]
    review = candidate.get("review") or {}
    if row["status"] == "COMPOUND" and review.get("role_row_checked") is True and mentions:
        # '교장 및 교감'처럼 한 칸에 두 역할이 적힌 행: 검수자가 '같은 행의 역할'을 확인하면 두 역할 모두 수행자다.
        # (사전의 compound_mentions 규칙: 사람 검수 후 행동마다 두 역할 모두에 DONE_BY를 둘지 정한다.)
        bases = {m["basis"] for m in mentions}
        basis = "EXAMPLE" if "EXAMPLE" in bases else next(iter(bases))
        return [{"role_id": role_id, "basis": basis, "role_text": chunk["role_label"], "method": "COMPOUND_ROW"}
                for role_id in row["candidates"]], [], []
    if row["status"] != "RESOLVED":
        roles, reasons = [], []
        for mention, result in mapped:
            if result["status"] != "RESOLVED":
                reasons.append(f"역할 연결 실패({result['status']}): {mention['role_text']} - "
                               f"{result['reason']}")
            elif result["id"] not in {r["role_id"] for r in roles}:
                roles.append({"role_id": result["id"], "basis": mention["basis"],
                              "role_text": mention["role_text"], "method": result["method"]})
        return roles, [], reasons
    if not mentions:
        return [], [], []
    same = [(m, r) for m, r in mapped if r["status"] == "RESOLVED" and r["id"] == row["id"]]
    others = [(m, r) for m, r in mapped if (m, r) not in same]
    dropped = [{"role_text": m["role_text"], "status": r["status"], "role_id": r["id"],
                "why": "표 행 담당이 아닌 표기 - 수신자·관련자로 보고 DONE_BY에서 제외"}
               for m, r in others]
    if same:
        mention, result = same[0]
        return [{"role_id": row["id"], "basis": mention["basis"], "role_text": mention["role_text"],
                 "method": result["method"]}], dropped, []
    # '사전에 없음'만 대신한다. 학교 밖 주체(EXCLUDED)·복합 표기(COMPOUND)는 대신하지 않는다.
    if all(r["status"] == "UNRESOLVED" for _, r in others) and review.get("role_row_checked") is True:
        bases = {m["basis"] for m, _ in others}
        basis = "EXAMPLE" if "EXAMPLE" in bases else next(iter(bases))
        return [{"role_id": row["id"], "basis": basis, "role_text": chunk["role_label"],
                 "method": "ROW_LABEL"}], dropped, []
    names = [m["role_text"] for m, r in others if r["status"] == "RESOLVED"]
    reason = (f"표 행 담당({chunk.get('role_label')})이 아닌 역할만 수행자로 추출됨: {names}"
              " - 행동 문장이 이 행의 담당 행동인지 확인 필요")
    return [], dropped, [reason]


def precheck(artifacts: dict, raw_texts: dict[str, str], books: dict) -> list[dict]:
    """검수 전 참고용: 사람 검수를 빼고 B01 형식·B02 원문·B03 사전 검사만 돌린 결과.

    여기서 걸리는 후보는 승인해도 정규화 단계에서 보류된다. 통과해도 의미 검수는 따로 필요하다.
    """
    chunks_by_id = {c["chunk_id"]: c for c in artifacts["chunks"]}
    chunk_problems = {c["chunk_id"]: chunk_reasons(c, raw_texts) for c in artifacts["chunks"]}
    rows = []
    for index, raw in enumerate(artifacts["candidates"], 1):
        wrapper = {"raw": raw}
        reasons = shape_reasons(raw)
        concept = None
        roles = []
        if not reasons:
            reasons += validate_source(wrapper, chunks_by_id, chunk_problems)
        if not reasons:
            mapping, map_reasons = map_candidate(wrapper, chunks_by_id[raw["source_chunk_id"]], books)
            reasons += map_reasons
            concept, roles = mapping["concept"], mapping["roles"]
        chunk = chunks_by_id.get(raw.get("source_chunk_id")) or {}
        stage, stage_note = settle_stage(raw.get("stage"), chunk)
        modality, modality_note = settle_modality(raw.get("modality"), raw.get("evidence"))
        rows.append({"candidate_index": index, "source_chunk_id": raw.get("source_chunk_id"),
                     "action_text": raw.get("action_text"),
                     "roles": [f"{r['role_id']}({r['basis']})" for r in roles],
                     "concept": (concept or {}).get("id") or (concept or {}).get("status"),
                     "stage": stage, "modality": modality,
                     "corrections": [n for n in (stage_note, modality_note) if n],
                     "auto_reasons": reasons})
    return rows


STAGE_WORDS = {"PREVENTION": r"예방단계", "RESPONSE_1": r"(?:대응)?제?1단계", "RESPONSE_2": r"(?:대응)?제?2단계",
               "RESPONSE_3": r"(?:대응)?제?3단계", "RECOVERY": r"복구단계"}
MODALITY_MARKERS = {
    "MANDATORY": r"해야|하여야|반드시|의무|금지|필수",
    "RECOMMENDED": r"권장|권고|바람직",
    "DISCRETIONARY": r"할 수 있|재량|자율적|선택",
}
SECTION_NOTE = "이 표가 놓인 본문 절:"


def settle_stage(raw_stage: str, chunk: dict) -> tuple[str, str | None]:
    """모델이 단계를 모르면(UNSPECIFIED) 표 제목·본문 절 경로에 단계가 정확히 하나 적혀 있을 때 그것을 쓴다.

    stage는 '원문이 행동을 어느 단계 아래에 배치했는가'를 기록하는 값이다(온톨로지 4절 규칙). 표가 '4. 대응 제3단계'
    절 아래에 있으면 그 표의 행동은 제3단계에 배치된 것이다. 본문 절 경로는 변환본의 제목에서 기계적으로 만든 값이다
    (모델 출력이 아님). 모델이 이미 단계를 정했으면 바꾸지 않는다. 반환: (단계, 보정 사유 또는 None).
    """
    if raw_stage != "UNSPECIFIED":
        return raw_stage, None
    paths = [n["text"].split(SECTION_NOTE, 1)[1] for n in chunk.get("editorial_context") or []
             if SECTION_NOTE in n.get("text", "")]
    head = re.sub(r"\s", "", " ".join(paths) + " " + (chunk.get("section") or ""))
    found = [stage for stage, pattern in STAGE_WORDS.items() if re.search(pattern, head)]
    if len(found) == 1:
        return found[0], f"UNSPECIFIED→{found[0]} (표가 놓인 본문 절: {' '.join(paths)[:60]})"
    return raw_stage, None


def settle_modality(raw_modality: str, evidence: str) -> tuple[str, str | None]:
    """원문 근거에 강도 표현이 없으면 UNSPECIFIED로 낮춘다(온톨로지 4절: 법률상 의무로 단정하지 않는다)."""
    marker = MODALITY_MARKERS.get(raw_modality)
    if marker and not re.search(marker, evidence or ""):
        return "UNSPECIFIED", f"{raw_modality}→UNSPECIFIED (근거 문장에 강도 표현 없음)"
    # '원칙적으로 금지'처럼 원칙을 밝히는 표현은 예외를 열어 두므로 의무로 단정하지 않는다(사용자 결정 11/05).
    if raw_modality == "MANDATORY" and re.search("원칙적으로|원칙적", evidence or ""):
        return "UNSPECIFIED", "MANDATORY→UNSPECIFIED (원칙적으로: 예외를 열어 둔 표현)"
    return raw_modality, None


def normalize_candidates(ready: list[dict], chunks: list[dict], raw_texts: dict[str, str],
                         books: dict) -> tuple[list[dict], list[dict], dict[str, list[str]]]:
    """B01 통과 후보에 B02·B03을 적용한다. (정규화 목록, 보류 목록, 청크별 문제)를 돌려준다."""
    chunks_by_id = {c["chunk_id"]: c for c in chunks}
    chunk_problems = {c["chunk_id"]: chunk_reasons(c, raw_texts) for c in chunks}
    normalized, held = [], []
    for candidate in ready:
        reasons = validate_source(candidate, chunks_by_id, chunk_problems)
        row = deepcopy(candidate)
        if not reasons:
            chunk = chunks_by_id[candidate["source_chunk_id"]]
            mapping, map_reasons = map_candidate(candidate, chunk, books)
            reasons += map_reasons
            raw = candidate["raw"]
            stage, stage_note = settle_stage(raw["stage"], chunk)
            modality, modality_note = settle_modality(raw["modality"], raw["evidence"])
            row.update({
                "scope": raw["scope"], "stage": stage, "step_order": raw["step_order"],
                "when_text": raw["when_text"], "timing_text": raw["timing_text"],
                "modality": modality, "evidence": raw["evidence"],
                "evidence_offset": evidence_offset(raw, chunk),
                "corrections": [n for n in (stage_note, modality_note) if n], **mapping,
            })
        row["hold_reasons"] = reasons
        (held if reasons else normalized).append(row)
    return normalized, held, chunk_problems
