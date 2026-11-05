"""11/05 B02·B03·B05 단위 검사(DB 없이). 실DB 검사(T08·T09 등)는 notebooks/school/school_action_tool.ipynb."""

import copy

import pytest

from index.school.dictionaries import load_dictionaries
from rag.school.action_tool import action_path, plan_action_task, records_to_tool_result, retrieve_action_records
from rag.school.contract_v2_local import (
    ContractError,
    split_ref,
    validate_query_input,
    validate_query_output,
    validate_tool_input,
    validate_tool_result,
)

ALLOWED = ["moe_school_manual_v3", "gyeonggi_school_guide_2025"]


@pytest.fixture(scope="module")
def books():
    return load_dictionaries()


def tool_input(question, **extra):
    return {"question": question, "task": "학교가 수행하는 행동, 역할, 단계 및 근거 찾기", "region": "GYEONGGI",
            "allowed_document_ids": list(ALLOWED), **extra}


def good_result():
    return {"tool_name": "action_search", "status": "OK",
            "findings": [{"claim": "주장", "chunk_ids": ["moe_school_manual_v3:p0036:t01r01"],
                          "action_ids": ["moe_school_manual_v3:p0036:a01"], "disease_ids": [], "concept_ids": []}],
            "citations": [{"document_id": "moe_school_manual_v3", "chunk_id": "moe_school_manual_v3:p0036:t01r01",
                           "pdf_page": 36, "printed_page": 22, "excerpt": "원문"}],
            "graph_paths": [{"edges": [{"source_ref": "SchoolAction:moe_school_manual_v3:p0036:a01",
                                        "relationship_type": "SUPPORTED_BY",
                                        "target_ref": "Chunk:moe_school_manual_v3:p0036:t01r01"}]}],
            "warnings": []}


# ---------------------------------------------------------------- B02 계약

def test_query_input_accepts_request_id_and_rejects_bad_preferred():
    query = {"question": "질문", "region": "GYEONGGI", "allowed_document_ids": ALLOWED, "request_id": "demo-01"}
    validate_query_input(query)
    with pytest.raises(ContractError):
        validate_query_input({**query, "preferred_document_ids": ["kdca_measles_2026"]})
    with pytest.raises(ContractError):
        validate_query_input({**query, "domain": "school"})           # v1.2 필드는 계약 밖
    with pytest.raises(ContractError):
        validate_query_input({**query, "region": "SEOUL"})


def test_tool_input_requires_task_and_positive_int_top_k():
    validate_tool_input(tool_input("질문", top_k=5))
    with pytest.raises(ContractError):
        validate_tool_input({k: v for k, v in tool_input("질문").items() if k != "task"})
    with pytest.raises(ContractError):
        validate_tool_input(tool_input("질문", top_k=True))
    with pytest.raises(ContractError):
        validate_tool_input(tool_input("질문", context_entity_ids=["CONTACT_GUARDIAN"]))   # '라벨:ID' 아님


def test_tool_result_checks():
    request = tool_input("질문")
    validate_tool_result(good_result(), request, expected_tool="action_search")
    with pytest.raises(ContractError):
        validate_tool_result(good_result(), request, expected_tool="legal_search")    # 호출 이름 ≠ 반환 이름
    cases = []
    bad = good_result(); bad["citations"][0]["pdf_page"] = True; cases.append(bad)          # bool 쪽수
    bad = good_result(); bad["citations"][0]["document_id"] = "kdca_measles_2026"; cases.append(bad)   # 허용 밖
    bad = good_result(); bad["findings"][0]["chunk_ids"] = ["x:p0001:b01"]; cases.append(bad)  # 누락 Citation
    bad = good_result(); bad["status"] = "NO_EVIDENCE"; cases.append(bad)                   # 근거 없음인데 주장
    bad = good_result(); bad["findings"] = []; cases.append(bad)                            # OK인데 Finding 없음
    bad = good_result(); bad["graph_paths"][0]["edges"][0]["source_ref"] = "SchoolAction"; cases.append(bad)
    bad = good_result(); bad["findings"][0]["action_ids"] = ["SchoolAction:moe_school_manual_v3:p0036:a01"]
    cases.append(bad)                                                                       # 라벨 접두어
    bad = good_result(); bad["domain"] = "school"; cases.append(bad)                        # 레거시 필드 누출
    for case in cases:
        with pytest.raises(ContractError):
            validate_tool_result(case, request)


def test_split_ref_uses_first_colon_only():
    assert split_ref("SchoolAction:moe_school_manual_v3:p0036:a01") == ("SchoolAction", "moe_school_manual_v3:p0036:a01")


def test_query_output_rules():
    request = {"question": "q", "region": "GYEONGGI", "allowed_document_ids": ALLOWED}
    result = good_result()
    output = {"response_type": "DOMAIN", "status": "ANSWERED", "answer": "a", "findings": result["findings"],
              "citations": result["citations"], "action_ids": ["moe_school_manual_v3:p0036:a01"], "warnings": [],
              "tools_used": ["action_search"], "search_rounds": 1}
    validate_query_output(output, request)
    for change in ({"search_rounds": 2}, {"action_ids": []}, {"response_type": "GENERAL"},
                   {"status": "INSUFFICIENT_EVIDENCE"}, {"tools_used": ["fake_tool"]}):
        with pytest.raises(ContractError):
            validate_query_output({**output, **change}, request)
    general = {**output, "response_type": "GENERAL", "findings": [], "citations": [], "action_ids": [],
               "tools_used": [], "search_rounds": 0}
    validate_query_output(general, request)


# ---------------------------------------------------------------- B03 계획

def test_plan_reads_roles_without_disease(books):
    plan = plan_action_task(tool_input("학교에서 유증상 학생이 발견되면 담임교사와 보건교사는 각각 무엇을 확인하나요?"), books)
    assert plan["roles"] == ["HOMEROOM_TEACHER", "HEALTH_TEACHER"] and plan["stages"] == ["RESPONSE_1"]
    assert plan["diseases"] == [] and plan["broad"]


def test_plan_does_not_take_recipient_or_health_center_as_performer(books):
    plan = plan_action_task(tool_input("담임교사는 보건교사에게 무엇을 알려야 하나요?"), books)
    assert plan["roles"] == ["HOMEROOM_TEACHER"] and plan["recipients"] == ["HEALTH_TEACHER"]
    plan = plan_action_task(tool_input("홍역 학생이 확인되면 학교는 보건소 신고 공문을 어떻게 보내나요?"), books)
    assert "HEALTH_TEACHER" not in plan["roles"] and plan["diseases"] == ["MEASLES"]


def test_plan_splits_joined_roles_and_reads_recovery(books):
    plan = plan_action_task(tool_input("유행의심 상황에서 교장·교감은 무엇을 하나요?"), books)
    assert {"PRINCIPAL", "VICE_PRINCIPAL"} <= set(plan["roles"])
    plan = plan_action_task(tool_input("유행이 끝나면 학교는 어떤 후속 조치를 하나요?"), books)
    assert plan["stages"] == ["RECOVERY"]


def test_plan_marks_legal_duty_and_compare_as_unresolved(books):
    plan = plan_action_task(tool_input("성홍열도 학교장이 보건소에 신고해야 하나요?"), books)
    assert any("legal_search" in u for u in plan["unresolved"])
    plan = plan_action_task(tool_input("두 문서는 보호자 연락을 각각 어떻게 설명하나요?"), books)
    assert any("crossdoc" in u for u in plan["unresolved"])


def test_plan_context_entity_ids_split_on_first_colon(books):
    plan = plan_action_task(tool_input("무엇을 하나요?", context_entity_ids=["SchoolRole:HEALTH_TEACHER",
                                                                         "Disease:NOT_A_DISEASE"]), books)
    assert "HEALTH_TEACHER" in plan["roles"]
    assert any("NOT_A_DISEASE" in u for u in plan["unresolved"])


# ---------------------------------------------------------------- B04·B05 조립 (DB 없이)

def test_allowed_empty_returns_no_rows_without_db(books):
    deps = {"driver": None, "books": books, "active_pages": {}}
    request = tool_input("질문", allowed_document_ids=[])
    plan = plan_action_task(request, books)
    assert retrieve_action_records(request, plan, deps)["actions"] == []


def make_action(chunk_ids, roles=({"role_id": "HOMEROOM_TEACHER", "name": "담임교사", "basis": "EXAMPLE"},)):
    evidence = [{"action_id": "moe_school_manual_v3:p0036:a01", "document_id": "moe_school_manual_v3",
                 "title": "교육부", "chunk_id": cid, "text": f"| 담임 | {cid} 원문 |", "section": "표",
                 "pdf_page": 36, "printed_page": 22} for cid in chunk_ids]
    return {"action_id": "moe_school_manual_v3:p0036:a01", "action_text": "보호자에게 연락한다.",
            "document_id": "moe_school_manual_v3", "chunk_id": chunk_ids[0], "stage": "RESPONSE_1", "step_order": 1,
            "when_text": None, "timing_text": None, "modality": "UNSPECIFIED", "roles": list(roles),
            "concept_ids": ["CONTACT_GUARDIAN"], "disease_ids": [], "evidence": evidence, "via": ["role"],
            "score": 9.0}


def test_multi_chunk_action_keeps_all_evidence_and_real_edges(books):
    request = tool_input("담임교사는 무엇을 하나요?")
    plan = plan_action_task(request, books)
    action = make_action(["moe_school_manual_v3:p0036:t01r01", "moe_school_manual_v3:p0036:t01r02"])
    result = records_to_tool_result({"actions": [action], "quotes": []}, request, plan)
    validate_tool_result(result, request, expected_tool="action_search")
    assert result["findings"][0]["chunk_ids"] == [c["chunk_id"] for c in action["evidence"]]
    assert len(result["citations"]) == 2
    types = [e["relationship_type"] for e in result["graph_paths"][0]["edges"]]
    assert types.count("SUPPORTED_BY") == 2 and types.count("HAS_CHUNK") == 2
    assert {"DONE_BY", "INSTANCE_OF"} <= set(types)
    # HAS_CHUNK은 Document → Chunk 저장 방향 그대로
    has_chunk = next(e for e in result["graph_paths"][0]["edges"] if e["relationship_type"] == "HAS_CHUNK")
    assert has_chunk["source_ref"].startswith("Document:") and has_chunk["target_ref"].startswith("Chunk:")


def test_role_without_basis_is_not_drawn_as_done_by():
    action = make_action(["moe_school_manual_v3:p0036:t01r01"],
                         roles=({"role_id": "HOMEROOM_TEACHER", "name": "담임교사", "basis": None},))
    assert "DONE_BY" not in {e["relationship_type"] for e in action_path(action)["edges"]}


def test_empty_records_give_no_evidence(books):
    request = tool_input("담임교사는 무엇을 하나요?")
    result = records_to_tool_result({"actions": [], "quotes": []}, request, plan_action_task(request, books))
    validate_tool_result(result, request)
    assert result["status"] == "NO_EVIDENCE" and not result["findings"] and result["warnings"]


def test_missing_asked_role_makes_partial(books):
    request = tool_input("담임교사와 보건교사는 각각 무엇을 하나요?")
    result = records_to_tool_result({"actions": [make_action(["moe_school_manual_v3:p0036:t01r01"])], "quotes": []},
                                    request, plan_action_task(request, books))
    assert result["status"] == "PARTIAL"
    assert any("HEALTH_TEACHER" in w for w in result["warnings"])


def test_scenario_only_evidence_is_partial(books):
    request = tool_input("담임교사는 무엇을 하나요?")
    action = make_action(["moe_school_manual_v3:p0036:t01r01"])
    action = copy.deepcopy(action)
    for chunk in action["evidence"]:
        chunk["section"] = "모의 훈련 시나리오: 대응 1단계"
    result = records_to_tool_result({"actions": [action], "quotes": []}, request, plan_action_task(request, books))
    assert result["status"] == "PARTIAL"
