"""B01~B05 정규화·ID 고정·적재 패키지 검사.

인공 Markdown 픽스처만 쓴다(data/raw는 Git 제외라 팀원 PC에 없을 수 있음). Neo4j·LLM 없이 실행한다.
문구는 인공 예시이며 공식 지침이 아니다.
"""

import copy
import hashlib
import json
import math
from pathlib import Path

import pytest

from index.school.bundle import BundleError, build_load_bundle, validate_bundle
from index.school.dictionaries import load_dictionaries
from index.school.id_map import (
    IdMapError,
    IdRegistry,
    attach_official_ids,
    document_node,
    load_manifest,
)
from index.school.normalize import (
    ArtifactError,
    candidate_key,
    load_artifacts,
    normalize_candidates,
    read_jsonl,
    select_approved,
    write_jsonl,
)

ROOT = Path(__file__).resolve().parents[3]

MOE_MD = """# 인공 표 작업본

## PDF 36쪽 · 인쇄 22쪽 · 인공 시나리오: 대응 1단계 주요 조치사항

편집 메모: 실제 역할은 예시입니다.

| 담당 | 주요 조치사항 |
|---|---|
| 담임교사 | 1) 보건교사에게 학생 상태를 전달 2) 교실 환기 3) 학생과 동행하여 보건실로 이동함 |
| 보건(담당)교사 | 1) 도착한 즉시 손을 씻도록 요청 |
"""
GG_MD = """# 인공 표 작업본

## PDF 54페이지 (인쇄 44쪽) · 인공 시나리오 단계 1 주요 조치사항

| 담당 | 주요 조치사항 |
|---|---|
| 담임교사 | 1) 보건교사에게 학생 상태를 전달 2) 교실 환기 |
"""
SOURCES = {"moe.md": ("TEMP:school_manual", "school_manual:tables", MOE_MD, 36, 22),
           "gg.md": ("TEMP:gyeonggi_guide", "gyeonggi_guide:tables", GG_MD, 54, 44)}


def make_chunk(path, role, number, text=None):
    document_id, source_id, default_text, page, printed = SOURCES[path]
    text = text or default_text
    title = next(line for line in text.splitlines(keepends=True) if line.startswith("## "))
    header = "| 담당 | 주요 조치사항 |\n"
    row = next(line for line in text.splitlines(keepends=True) if line.startswith(f"| {role} |"))
    parts = []
    for label, value in (("title", title), ("header", header), ("row", row)):
        start = text.index(value)
        parts.append({"part": label, "source_id": source_id, "block_id": "B0001",
                      "start_offset": start, "end_offset": start + len(value), "text": value})
    return {"document_id": document_id, "chunk_id": f"{document_id}:B0001:{number:02d}",
            "id_status": "TEMP_UNAPPROVED", "source_id": source_id, "source_path": path,
            "content_hash": hashlib.sha256(text.encode()).hexdigest(), "pdf_page": page,
            "printed_page": printed, "page_verification": "VERIFIED_PDF", "section": title,
            "role_label": role, "source_parts": parts,
            "editorial_context": [{"text": "편집 메모: 실제 역할은 예시입니다.\n"}],
            "text": "".join(p["text"] for p in parts)}


def make_candidate(chunk, evidence, concept="CONTACT_HEALTH_TEACHER", role="담임교사", **extra):
    return {"document_id": chunk["document_id"], "source_chunk_id": chunk["chunk_id"],
            "model": "fixture", "prompt_version": "fixture", "review_status": "PENDING",
            "action_text": f"[인공] {evidence}", "scope": "COMMON", "stage": "RESPONSE_1",
            "step_order": 1, "when_text": None, "timing_text": None, "modality": "UNSPECIFIED",
            "roles": [{"role_text": role, "basis": "EXAMPLE", "basis_evidence": role}],
            "named_diseases": [], "concept_candidate": concept, "evidence": evidence,
            "check_reasons": ["예시 단서와 역할 배정의 관계를 PDF에서 확인 필요"],
            "quote_check_pass": False, **extra}


def approve(candidate, index, **override):
    return {"document_id": candidate["document_id"], "source_chunk_id": candidate["source_chunk_id"],
            "candidate_index": index, "review_status": "APPROVED", "reviewer": "검수자",
            "reviewed_at": "2026-11-04T00:00:00+00:00", "reason": "",
            "pdf_source_checked": True, "role_row_checked": True, "example_basis_checked": True,
            "stage_condition_checked": True, "concept_checked": True,
            "evidence_location": candidate["source_chunk_id"],
            "candidate_fingerprint": candidate_key(candidate), "edited_action_text": None,
            **override}


@pytest.fixture
def world():
    chunks = [make_chunk("moe.md", "담임교사", 1), make_chunk("moe.md", "보건(담당)교사", 2),
              make_chunk("gg.md", "담임교사", 3)]
    moe, _, gg = chunks
    candidates = [
        make_candidate(moe, "교실 환기", concept="VENTILATE_DISINFECT"),
        make_candidate(moe, "보건교사에게 학생 상태를 전달"),
        make_candidate(moe, "학생과 동행하여 보건실로 이동함", concept="NEW_CONCEPT_CANDIDATE"),
        make_candidate(gg, "보건교사에게 학생 상태를 전달"),
    ]
    reviews = [approve(c, i) for i, c in enumerate(candidates, 1)]
    artifacts = {"run_dir": "run_fixture", "runs": [{"status": "SUCCESS"}], "chunks": chunks,
                 "candidates": candidates, "reviews": reviews}
    raw_texts = {path: spec[2] for path, spec in SOURCES.items()}
    return {"artifacts": artifacts, "raw": raw_texts, "books": load_dictionaries(),
            "manifest": load_manifest(ROOT)}


def run_pipeline(world, registry=None, policy="hold"):  # 기존 검사는 hold 정책 기준
    ready, held = select_approved(world["artifacts"])
    normalized, held2, _ = normalize_candidates(ready, world["artifacts"]["chunks"], world["raw"],
                                                world["books"])
    registry = registry or IdRegistry(path=Path("unused.json"))
    actions, held3, official = attach_official_ids(normalized, world["artifacts"]["chunks"],
                                                   world["raw"], world["manifest"], registry)
    docs = [document_node(d, world["manifest"]) for d in {o["document_id"] for o in official.values()}]
    bundle = build_load_bundle(actions, list(official.values()), docs, world["books"],
                               concept_policy=policy)
    return {"ready": ready, "held": held + held2 + held3, "actions": actions,
            "official": official, "bundle": bundle, "registry": registry}


# ---------------------------------------------------------------- B01

def test_all_approved_candidates_flow_through(world):
    result = run_pipeline(world)
    assert len(result["ready"]) == 4 and not result["held"]
    assert len(result["actions"]) == 4


def test_unreviewed_candidate_is_held_not_lost(world):
    world["artifacts"]["reviews"][0].update(review_status="PENDING")
    world["artifacts"]["reviews"][1].update(concept_checked=False)
    ready, held = select_approved(world["artifacts"])
    assert len(ready) + len(held) == 4 and len(held) == 2
    assert held[0]["raw"] == world["artifacts"]["candidates"][0]
    assert any("미승인" in r for r in held[0]["hold_reasons"])
    assert any("검수 항목 미완료" in r for r in held[1]["hold_reasons"])


def test_review_linked_by_fingerprint_not_row_number(world):
    # 검수 뒤 후보가 바뀌면(지문 변경) 행 번호가 같아도 연결하지 않고 알린다.
    world["artifacts"]["candidates"][0]["action_text"] = "바뀐 문장"
    with pytest.raises(ArtifactError, match="연결되지 않는 검수 기록"):
        select_approved(world["artifacts"])


def test_duplicate_review_is_held(world):
    world["artifacts"]["reviews"].append(copy.deepcopy(world["artifacts"]["reviews"][0]))
    _, held = select_approved(world["artifacts"])
    assert any("여러 개" in r for r in held[0]["hold_reasons"])


def test_unknown_check_reason_is_held(world):
    candidate = world["artifacts"]["candidates"][0]
    candidate["check_reasons"].append("새로 생긴 확인 사유")
    world["artifacts"]["reviews"][0] = approve(candidate, 1)
    _, held = select_approved(world["artifacts"])
    assert any("해소 근거를 알 수 없는" in r for r in held[0]["hold_reasons"])


def test_edited_action_text_is_used_and_raw_kept(world):
    world["artifacts"]["reviews"][0]["edited_action_text"] = "교실을 환기한다."
    ready, _ = select_approved(world["artifacts"])
    assert ready[0]["action_text"] == "교실을 환기한다." and ready[0]["action_text_edited"]
    assert ready[0]["raw"]["action_text"] == "[인공] 교실 환기"


def test_shape_violation_is_held(world):
    candidate = world["artifacts"]["candidates"][0]
    candidate.update(stage="STAGE_ONE", step_order=0)
    candidate["roles"][0]["basis"] = None
    world["artifacts"]["reviews"][0] = approve(candidate, 1)
    _, held = select_approved(world["artifacts"])
    reasons = " ".join(held[0]["hold_reasons"])
    assert "stage" in reasons and "step_order" in reasons and "basis" in reasons


def test_strict_jsonl_roundtrip(tmp_path):
    path = write_jsonl(tmp_path / "ok.jsonl", [{"a": None, "b": [1, 2]}])
    assert read_jsonl(path) == [{"a": None, "b": [1, 2]}]
    with pytest.raises(ValueError):
        write_jsonl(tmp_path / "nan.jsonl", [{"a": math.nan}])
    (tmp_path / "bad.jsonl").write_text('{"a": NaN}\n', encoding="utf-8")
    with pytest.raises(ArtifactError):
        read_jsonl(tmp_path / "bad.jsonl")


def test_failed_run_folder_rejected(tmp_path):
    for name in ("chunks", "steps_raw", "review"):
        (tmp_path / f"{name}.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "run.jsonl").write_text(json.dumps({"status": "CALL_FAILED"}) + "\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="성공하지 않은"):
        load_artifacts(tmp_path)


# ---------------------------------------------------------------- B02

def _held_reasons(world):
    ready, _ = select_approved(world["artifacts"])
    _, held, _ = normalize_candidates(ready, world["artifacts"]["chunks"], world["raw"], world["books"])
    return [" ".join(h["hold_reasons"]) for h in held]


@pytest.mark.parametrize("evidence", [
    "도착한 즉시 손을 씻도록 요청",            # 다른 행(보건교사)의 문장
    "주요 조치사항 | 담임교사",                # 머리글과 행을 이어 붙인 문장
    "편집 메모: 실제 역할은 예시입니다.",       # 편집 메모
    "보건교사에게 학생 상태를 즉시 전달",       # 조작된 인용
])
def test_quote_must_be_in_the_same_row(world, evidence):
    candidate = world["artifacts"]["candidates"][1]
    candidate["evidence"] = evidence
    world["artifacts"]["reviews"][1] = approve(candidate, 2)
    assert any("표 행 원문에 연속으로 없음" in r for r in _held_reasons(world))


def test_missing_or_foreign_chunk_is_held(world):
    first, second = world["artifacts"]["candidates"][:2]
    first["source_chunk_id"] = "TEMP:school_manual:B9999:01"
    second["document_id"] = "TEMP:gyeonggi_guide"
    world["artifacts"]["reviews"][0] = approve(first, 1)
    world["artifacts"]["reviews"][1] = approve(second, 2)
    reasons = _held_reasons(world)
    assert any("청크 없음" in r for r in reasons) and any("문서 소속 불일치" in r for r in reasons)


def test_changed_raw_file_or_unverified_page_is_held(world):
    world["raw"]["moe.md"] = MOE_MD.replace("교실 환기", "교실 환기!")
    world["artifacts"]["chunks"][2]["page_verification"] = "PENDING_PDF_COMPARISON"
    reasons = _held_reasons(world)
    assert sum("해시" in r for r in reasons) == 3
    assert any("PDF 쪽 대조 미완료" in r for r in reasons)


# ---------------------------------------------------------------- B03

def test_roles_resolve_and_recipient_is_not_performer(world):
    result = run_pipeline(world)
    sent = next(a for a in result["actions"] if a["evidence"] == "보건교사에게 학생 상태를 전달"
                and a["document_id"] == "moe_school_manual_v3")
    assert [r["role_id"] for r in sent["roles"]] == ["HOMEROOM_TEACHER"]   # 보건교사는 수신자


@pytest.mark.parametrize("role", ["보건교사", "교장 및 교감", "보건소", "생활부장"])
def test_non_row_roles_alone_are_held(world, role):
    # 담임 행인데 다른 학교 역할·복합 표기·학교 밖 주체·미등록 역할만 수행자로 나오면 보류.
    candidate = world["artifacts"]["candidates"][0]
    candidate["roles"] = [{"role_text": role, "basis": "EXAMPLE", "basis_evidence": "담임교사"}]
    world["artifacts"]["reviews"][0] = approve(candidate, 1)
    assert any("아닌 역할만 수행자로 추출됨" in r for r in _held_reasons(world))


def test_recipient_roles_are_dropped_when_row_role_present(world):
    candidate = world["artifacts"]["candidates"][1]          # '보건교사에게 학생 상태를 전달'
    candidate["roles"].append({"role_text": "보건교사", "basis": "EXAMPLE", "basis_evidence": "담임교사"})
    world["artifacts"]["reviews"][1] = approve(candidate, 2)
    action = next(a for a in run_pipeline(world)["actions"] if a["candidate_index"] == 2
                  and a["document_id"] == "moe_school_manual_v3")
    assert [r["role_id"] for r in action["roles"]] == ["HOMEROOM_TEACHER"]
    assert action["dropped_role_mentions"][0]["role_text"] == "보건교사"


def test_unregistered_role_uses_row_label_only_after_review(world):
    candidate = world["artifacts"]["candidates"][0]
    candidate["roles"] = [{"role_text": "담당 교사", "basis": "EXAMPLE", "basis_evidence": "담임교사"}]
    world["artifacts"]["reviews"][0] = approve(candidate, 1)
    action = next(a for a in run_pipeline(world)["actions"] if a["candidate_index"] == 1)
    assert action["roles"][0]["role_id"] == "HOMEROOM_TEACHER" and action["roles"][0]["method"] == "ROW_LABEL"
    world["artifacts"]["reviews"][0]["role_row_checked"] = False      # 검수 미확인이면 대신하지 않음
    assert any(h["candidate_index"] == 1 for h in run_pipeline(world)["held"])


def test_split_actions_from_same_evidence_get_separate_ids(world):
    chunk = world["artifacts"]["chunks"][0]
    pair = [make_candidate(chunk, "교실 환기", concept="VENTILATE_DISINFECT"),
            make_candidate(chunk, "교실 환기", concept="HEALTH_EDUCATION")]
    pair[1]["action_text"] = "[인공] 교실 환기 교육"
    world["artifacts"]["candidates"][0] = pair[0]
    world["artifacts"]["candidates"].append(pair[1])
    world["artifacts"]["reviews"][0] = approve(pair[0], 1)
    world["artifacts"]["reviews"].append(approve(pair[1], 5))
    result = run_pipeline(world)
    ids = [a["action_id"] for a in result["actions"] if a["evidence"] == "교실 환기"]
    assert len(set(ids)) == 2 and not result["held"]
    again = run_pipeline(world, result["registry"])                  # 재실행에도 같은 ID
    assert sorted(ids) == sorted(a["action_id"] for a in again["actions"] if a["evidence"] == "교실 환기")


def test_concept_and_disease_rules(world):
    first, second = world["artifacts"]["candidates"][:2]
    first["concept_candidate"] = "MADE_UP_CONCEPT"
    second["named_diseases"] = ["성홍열"]          # COMMON인데 질병
    world["artifacts"]["reviews"][0] = approve(first, 1)
    world["artifacts"]["reviews"][1] = approve(second, 2)
    reasons = _held_reasons(world)
    assert any("사전 밖 개념" in r for r in reasons)
    assert any("질병 추론 금지" in r for r in reasons)


# ---------------------------------------------------------------- B04

def test_official_ids_follow_page_order_not_input_order(world):
    forward = run_pipeline(world)
    world["artifacts"]["candidates"].reverse()
    world["artifacts"]["reviews"].reverse()
    backward = run_pipeline(world)
    ids = {a["candidate_key"]: a["action_id"] for a in forward["actions"]}
    assert ids == {a["candidate_key"]: a["action_id"] for a in backward["actions"]}
    moe = sorted((a["evidence_offset"], a["action_id"]) for a in forward["actions"]
                 if a["document_id"] == "moe_school_manual_v3")
    assert [aid for _, aid in moe] == [f"moe_school_manual_v3:p0036:a0{i}" for i in (1, 2, 3)]
    chunk_ids = sorted(o["chunk_id"] for o in forward["official"].values())
    assert chunk_ids == ["gyeonggi_school_guide_2025:p0054:t01r01",
                         "moe_school_manual_v3:p0036:t01r01", "moe_school_manual_v3:p0036:t01r02"]


def test_same_concept_in_two_documents_keeps_two_actions(world):
    result = run_pipeline(world)
    same = [a for a in result["actions"] if a["concept"]["id"] == "CONTACT_HEALTH_TEACHER"]
    assert len({a["action_id"] for a in same}) == 2 and len({a["document_id"] for a in same}) == 2


def test_registry_keeps_ids_when_candidates_are_added_or_held(world, tmp_path):
    first = run_pipeline(world, IdRegistry(path=tmp_path / "reg.json"))
    first["registry"].save()
    before = {a["candidate_key"]: a["action_id"] for a in first["actions"]}
    # 첫 후보를 보류하고, 같은 행의 새 후보를 추가해도 기존 번호는 그대로이고 새 번호는 뒤에 붙는다.
    world["artifacts"]["reviews"][0]["review_status"] = "HOLD"
    chunk = world["artifacts"]["chunks"][0]
    extra = make_candidate(chunk, "보건교사에게 학생 상태를 전달 2) 교실 환기")
    world["artifacts"]["candidates"].append(extra)
    world["artifacts"]["reviews"].append(approve(extra, 5))
    second = run_pipeline(world, IdRegistry.load(tmp_path / "reg.json"))
    after = {a["candidate_key"]: a["action_id"] for a in second["actions"]}
    assert all(after[k] == v for k, v in before.items() if k in after)
    assert after[candidate_key(extra)] == "moe_school_manual_v3:p0036:a04"
    assert all(a["action_id_status"] == "REUSED" for a in second["actions"]
               if a["candidate_key"] != candidate_key(extra))


def test_registry_detects_changed_row_text(world, tmp_path):
    first = run_pipeline(world, IdRegistry(path=tmp_path / "reg.json"))
    first["registry"].save()
    # 원문 행 내용이 바뀌었는데 위치(t01r01)는 같으면, 같은 ID를 조용히 재사용하지 않는다.
    changed = MOE_MD.replace("교실 환기", "교실 환기 및 소독")
    world["raw"]["moe.md"] = changed
    world["artifacts"]["chunks"][:2] = [make_chunk("moe.md", "담임교사", 1, text=changed),
                                        make_chunk("moe.md", "보건(담당)교사", 2, text=changed)]
    with pytest.raises(IdMapError, match="원문 행이 바뀜"):
        attach_official_ids([], world["artifacts"]["chunks"], world["raw"], world["manifest"],
                            IdRegistry.load(tmp_path / "reg.json"))


def test_printed_page_offset_mismatch_rejected(world):
    world["artifacts"]["chunks"][0]["printed_page"] = 23
    with pytest.raises(IdMapError, match="인쇄 쪽"):
        attach_official_ids([], world["artifacts"]["chunks"], world["raw"], world["manifest"],
                            IdRegistry(path=Path("unused.json")))


# ---------------------------------------------------------------- B05

def test_bundle_projects_only_ontology_properties(world):
    bundle = run_pipeline(world)["bundle"]
    assert set(bundle["nodes"]["SchoolAction"][0]) == {
        "action_id", "action_text", "scope", "stage", "step_order", "when_text", "timing_text",
        "modality"}
    dumped = json.dumps({"nodes": bundle["nodes"], "edges": bundle["edges"]}, ensure_ascii=False)
    for leaked in ("review_status", "candidate_key", "prompt_version", "TEMP:", "content_hash"):
        assert leaked not in dumped
    assert all(e["props"] == {"basis": "EXAMPLE"} for e in bundle["edges"] if e["type"] == "DONE_BY")


def test_concept_policy_hold_vs_omit_link(world):
    held = run_pipeline(world, policy="hold")["bundle"]
    omitted = run_pipeline(world, policy="omit_link")["bundle"]
    assert len(held["nodes"]["SchoolAction"]) == 3 and len(held["deferred"]) == 1
    assert len(omitted["nodes"]["SchoolAction"]) == 4 and not omitted["deferred"]
    instance = [e for e in omitted["edges"] if e["type"] == "INSTANCE_OF"]
    assert len(instance) == 3 and omitted["warnings"]


def _tamper(bundle, change):
    broken = copy.deepcopy(bundle)
    change(broken)
    return broken


@pytest.mark.parametrize("change, message", [
    (lambda b: next(e for e in b["edges"] if e["type"] == "DONE_BY")["props"].clear(), "basis"),
    (lambda b: b["edges"].append({**b["edges"][0], "type": "MENTIONS"}), "허용하지 않은 관계"),
    (lambda b: next(e for e in b["edges"] if e["type"] == "SUPPORTED_BY")
     .update(end_id="moe_school_manual_v3:p0036:t09r09"), "없는 끝점"),
    (lambda b: next(e for e in b["edges"] if e["type"] == "INSTANCE_OF")
     .update(end_id="NOT_IN_DICTIONARY"), "없는 끝점"),
    (lambda b: b["edges"].append({"type": "FOR_DISEASE", "start_label": "SchoolAction",
                                  "start_id": b["nodes"]["SchoolAction"][0]["action_id"],
                                  "end_label": "Disease", "end_id": "SCARLET_FEVER", "props": {}}),
     "NAMED_DISEASES가 아닌"),
    (lambda b: b["nodes"]["SchoolAction"][0].update(review_status="APPROVED"), "속성 목록 위반"),
    (lambda b: b["nodes"]["Chunk"][0].update(section="TEMP:x"), "TEMP"),
    (lambda b: b["edges"].append(copy.deepcopy(b["edges"][0])), "중복 관계"),
])
def test_bundle_rejects_rule_violations(world, change, message):
    bundle = run_pipeline(world)["bundle"]
    with pytest.raises(BundleError, match=message):
        validate_bundle(_tamper(bundle, change), world["books"])


def test_expected_keys_cover_all_nodes_and_edges(world):
    bundle = run_pipeline(world)["bundle"]
    assert len(bundle["expected"]["nodes"]) == sum(len(v) for v in bundle["nodes"].values())
    assert len(bundle["expected"]["edges"]) == len(bundle["edges"])
    assert ("SchoolRole", "HOMEROOM_TEACHER") in bundle["dictionary_refs"]


def test_explicit_basis_in_example_role_table_is_corrected(world):
    # 두 학교 문서의 역할표는 예시(온톨로지 5-1) - LLM이 EXPLICIT을 내도 EXAMPLE로 고친다.
    candidate = world["artifacts"]["candidates"][0]
    candidate["roles"][0]["basis"] = "EXPLICIT"
    world["artifacts"]["reviews"][0] = approve(candidate, 1)
    result = run_pipeline(world)
    action = next(a for a in result["actions"] if a["candidate_index"] == 1)
    assert action["roles"][0]["basis"] == "EXAMPLE" and "EXPLICIT→EXAMPLE" in action["roles"][0]["basis_corrected"]
    assert all(e["props"] == {"basis": "EXAMPLE"} for e in result["bundle"]["edges"] if e["type"] == "DONE_BY")


# ---------------------------------------------------------------- 역할표 확대에서 추가한 보정 규칙

from index.school.normalize import loose_find, settle_modality, settle_stage


def test_loose_quote_ignores_br_spaces_and_bullets():
    row = "|담임교사|대응<br>제3단계|1. 능동감시<br> - 매일 학급 학생들의 감염병(의심) 환자<br>추가 발생 여부 파악|"
    assert loose_find(row, "매일 학급 학생들의 감염병(의심) 환자 추가 발생 여부 파악") >= 0
    assert loose_find(row, "매일 학급 학생들의 감염병 의심 환자 추가 발생 여부를 파악") < 0     # 다른 글자는 거부
    assert row[loose_find(row, "능동감시"):].startswith("능동감시")


def test_evidence_across_br_is_accepted_and_offset_is_found(world):
    changed = MOE_MD.replace("1) 보건교사에게 학생 상태를 전달 2) 교실 환기", "1) 보건교사에게 학생 상태를<br>전달 2) 교실<br>환기")
    SOURCES["moe.md"] = (*SOURCES["moe.md"][:2], changed, 36, 22)
    try:
        chunk = make_chunk("moe.md", "담임교사", 1)
    finally:
        SOURCES["moe.md"] = (*SOURCES["moe.md"][:2], MOE_MD, 36, 22)
    world["raw"]["moe.md"] = changed
    world["artifacts"]["chunks"][0] = chunk
    candidate = make_candidate(chunk, "보건교사에게 학생 상태를 전달")      # <br> 없이 이어 쓴 인용
    world["artifacts"]["candidates"][0] = candidate
    world["artifacts"]["reviews"][0] = approve(candidate, 1)
    ready, _ = select_approved(world["artifacts"])
    normalized, held, _ = normalize_candidates(ready[:1], world["artifacts"]["chunks"], world["raw"],
                                               world["books"])
    assert not held and normalized[0]["evidence_offset"] >= 0


def test_stage_is_filled_only_from_section_path_and_only_when_unspecified():
    chunk = {"section": "[표 3-13] 밀접접촉자 파악 및 관리 담당자별 역할",
             "editorial_context": [{"text": "이 표가 놓인 본문 절: 4. 대응 제3단계: 학교 내 유행 확산 차단 > 나. 관련 조치사항 내용\n"}]}
    assert settle_stage("UNSPECIFIED", chunk)[0] == "RESPONSE_3"
    assert settle_stage("RESPONSE_2", chunk) == ("RESPONSE_2", None)          # 모델이 정한 단계는 유지
    two = {"section": "대응 제1단계와 제2단계", "editorial_context": []}
    assert settle_stage("UNSPECIFIED", two) == ("UNSPECIFIED", None)          # 단계가 둘이면 고르지 않음
    assert settle_stage("UNSPECIFIED", {"section": "표 3-2", "editorial_context": []})[0] == "UNSPECIFIED"


@pytest.mark.parametrize("modality, evidence, expected", [
    ("MANDATORY", "발생감시팀은 능동감시를 하고", "UNSPECIFIED"),
    ("MANDATORY", "혼자 이동하는 것은 원칙적으로 금지", "UNSPECIFIED"),
    ("MANDATORY", "반드시 보고해야 한다", "MANDATORY"),
    ("RECOMMENDED", "밀접접촉자를 파악하고 관리하는 것이 매우 중요", "UNSPECIFIED"),
    ("RECOMMENDED", "담당 교사는 담임교사 권장", "RECOMMENDED"),
    ("UNSPECIFIED", "아무 문장", "UNSPECIFIED"),
])
def test_modality_requires_wording_in_evidence(modality, evidence, expected):
    assert settle_modality(modality, evidence)[0] == expected


def test_compound_row_gives_both_roles_only_after_review(world):
    chunk = world["artifacts"]["chunks"][0]
    chunk["role_label"] = "교장 및 교감"
    candidate = make_candidate(chunk, "교실 환기", role="교장 및 교감")
    world["artifacts"]["candidates"][0] = candidate
    world["artifacts"]["reviews"][0] = approve(candidate, 1, role_row_checked=False)
    assert any(h["candidate_index"] == 1 for h in run_pipeline(world)["held"])      # 검수 항목 미완료 → 보류
    world["artifacts"]["reviews"][0] = approve(candidate, 1)
    ready, _ = select_approved(world["artifacts"])
    normalized, _, _ = normalize_candidates(ready, world["artifacts"]["chunks"], world["raw"], world["books"])
    action = next(a for a in normalized if a["candidate_index"] == 1)
    assert sorted(r["role_id"] for r in action["roles"]) == ["PRINCIPAL", "VICE_PRINCIPAL"]
    assert all(r["basis"] == "EXAMPLE" for r in action["roles"])
