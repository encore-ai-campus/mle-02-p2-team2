"""v2 Tool 회귀: 질병명/상황 구분, 다른 질병 배제, 조치 적합성 및 근거 충분성."""

import pytest

from index.school.dictionaries import load_dictionaries
from rag.school import action_tool
from rag.school.action_tool import _off_stage, plan_action_task, records_to_tool_result, retrieve_action_records
from rag.school.contract_v2_local import validate_tool_result
from rag.school.relevance import action_disease_mismatch, disease_mentions, evidence_diseases

DOC = "moe_school_manual_v3"
QUESTION = "유행성각결막염 걸렸을 때도 전파차단 위한 휴업 해야하나요?"
CLOSURE = ("전파 차단을 위한 휴업\n"
           "인플루엔자, 수두, 유행성이하선염, 수족구병, 유행성각결막염 등의 학생 빈발 감염병에 대해서는\n"
           "휴업은 권장하지 않음(단, 방역당국의 역학조사 결과 휴업을 권고하는 경우에는 실시)")


@pytest.fixture(scope="module")
def books():
    return load_dictionaries()


def request(question=QUESTION, **extra):
    return {"question": question, "task": "학교 행동과 근거 찾기", "region": "GYEONGGI",
            "allowed_document_ids": [DOC], **extra}


def hit(page, text, section="", rank=1):
    return {"document_id": DOC, "chunk_id": f"{DOC}:p{page:04d}:b01", "pdf_page": page,
            "printed_page": page - 14, "title": "교육부 매뉴얼", "text": text, "section": section, "rank": rank}


def quote(h):
    return {"hit": h, "excerpt": h["text"], "role": None, "stages": set(), "off_stage": False}


def action(h, text="휴업 실시 필요성을 검토한다.", diseases=()):
    return {"action_id": f"{DOC}:p{h['pdf_page']:04d}:a01", "action_text": text,
            "document_id": DOC, "chunk_id": h["chunk_id"], "section": h["section"],
            "scope": "COMMON", "stage": "UNSPECIFIED", "step_order": 1, "when_text": None,
            "timing_text": None, "modality": "UNSPECIFIED", "roles": [], "disease_ids": list(diseases),
            "concept_ids": ["SCHOOL_CLOSURE"], "evidence": [h]}


@pytest.mark.parametrize("question,disease,stage", [
    (QUESTION, "EKC", None),
    ("유행성 각결막염의 휴업 기준은?", "EKC", None),
    ("유행성이하선염의 등교중지 기준은?", "MUMPS", None),
    ("유행성각결막염이 유행하면 휴업하나요?", "EKC", ["RESPONSE_3"]),
    ("유행성이하선염 유행이 끝나면 무엇을 하나요?", "MUMPS", ["RECOVERY"]),
    ("유행성각결막염 대응 제2단계에서 무엇을 하나요?", "EKC", ["RESPONSE_2"]),
])
def test_disease_name_is_not_a_situation(question, disease, stage, books):
    original = request(question)
    plan = plan_action_task(original, books)
    assert plan["diseases"] == [disease]
    assert plan["stages"] == stage
    assert original["question"] == question


def test_ambiguous_eye_disease_alias_is_not_ekc(books):
    assert disease_mentions("눈병", books) == set()
    assert disease_mentions("급성 출혈성 결막염", books) == set()
    assert disease_mentions("Haemophilus influenzae", books) == set()  # Flu 부분 문자열 아님
    assert disease_mentions("Flu cases and Scarlet fever cases", books) == {"INFLUENZA", "SCARLET_FEVER"}


def test_disease_table_row_overrides_stale_section_and_other_rows(books):
    section = "차. 코로나바이러스감염증-19(COVID-19) > 감염병별 역학적 특성과 관리 방안"
    unknown = hit(221, "감염병별 역학적 특성과 관리 방안\n| 감염병 | 조치 |\n"
                  "| 급성 출혈성<br>결막염 | 격리 없이 개인위생수칙 준수 |", section)
    correct = hit(224, "감염병별 역학적 특성과 관리 방안\n| 감염병 | 조치 |\n"
                  "| 유행성<br>각결막염 | 개인위생수칙 준수 |", section)
    assert evidence_diseases(unknown, books) == set()  # 등록되지 않은 질병도 일반 근거로 통과시키지 않음
    assert evidence_diseases(correct, books) == {"EKC"}


def test_disease_section_supplies_context_but_action_mismatch_is_rejected(books):
    h = hit(202, "수건을 따로 사용한다.", "1. 학생 빈발 감염병 > 라. 유행성각결막염")
    assert evidence_diseases(h, books) == {"EKC"}
    wrong = action(h, diseases=["VARICELLA"])
    assert action_disease_mismatch(wrong, ["EKC"], books)
    # COMMON 속성만으로 특정 질병에 적용된다고 간주하지 않는다.
    general = action(hit(104, "휴업을 검토한다."))
    assert evidence_diseases(general["evidence"][0], books) is None


def retrieve_fixture(monkeypatch, books, req, hits, actions=()):
    monkeypatch.setattr(action_tool, "search_school_chunks", lambda *a, **k: {"hits": hits, "mode": "fulltext"})
    monkeypatch.setattr(action_tool, "expand_school_actions", lambda *a, **k: [])
    monkeypatch.setattr(action_tool, "scan_actions", lambda *a, **k: list(actions))
    monkeypatch.setattr(action_tool, "get_action_evidence",
                        lambda driver, ids, *a, **k: [dict(h, action_id=x["action_id"])
                                                     for x in actions if x["action_id"] in ids
                                                     for h in x["evidence"]])
    plan = plan_action_task(req, books)
    records = retrieve_action_records(req, plan, {"driver": None, "books": books, "active_pages": {}})
    return records, records_to_tool_result(records, req, plan)


def test_closure_keeps_direct_rule_and_exception_without_padding(monkeypatch, books):
    direct = hit(104, CLOSURE, "휴업 실시의 일반적 기준")
    hygiene = hit(201, "유행성각결막염 환자는 올바른 손 씻기를 실시한다.", "유행성각결막염")
    other = hit(221, "| 감염병 | 조치 |\n| 급성 출혈성<br>결막염 | 휴업을 검토한다. |")
    scenario = hit(40, "인플루엔자 모의훈련: 자체 휴업 검토", "인플루엔자 모의훈련 시나리오")
    irrelevant = action(hit(66, "유행 사실을 보호자에게 알린다."), "보호자에게 유행 사실을 알린다.")
    records, result = retrieve_fixture(monkeypatch, books, request(), [direct, hygiene, other],
                                      [action(scenario), irrelevant])
    validate_tool_result(result, request())
    assert result["status"] == "OK"
    assert len(result["findings"]) == len(result["citations"]) == 1
    assert "권장하지 않음" in result["findings"][0]["claim"]
    assert "역학조사 결과" in result["findings"][0]["claim"]
    reasons = next(log for log in records["log"] if log["step"] == "relevance_filter")
    assert {h["reason"] for h in reasons["rejected_hits"]} == {"disease_mismatch", "operation_mismatch"}
    assert {a["reason"] for a in reasons["rejected_actions"]} == {"disease_mismatch", "operation_mismatch"}


def test_unrelated_disease_only_is_no_evidence(monkeypatch, books):
    wrong = hit(183, "수두 발생 시 휴업을 검토한다.", "수두")
    _, result = retrieve_fixture(monkeypatch, books, request(), [wrong], [action(wrong, diseases=["VARICELLA"])])
    assert result["status"] == "NO_EVIDENCE"
    assert not result["findings"] and not result["citations"]


def test_general_rule_does_not_prove_disease_specific_applicability(books):
    req = request()
    general = hit(104, "전파 차단을 위한 휴업은 원칙적으로 권장하지 않는다.")
    result = records_to_tool_result({"actions": [], "quotes": [quote(general)]},
                                    req, plan_action_task(req, books))
    assert result["status"] == "PARTIAL"
    assert any("질병의 적용 근거" in w and "EKC" in w for w in result["warnings"])


def test_multiple_diseases_require_evidence_for_each(books):
    req = request("수두와 홍역의 휴업 기준은?")
    result = records_to_tool_result({"actions": [], "quotes": [quote(hit(104, "수두의 휴업 기준이다."))]},
                                    req, plan_action_task(req, books))
    assert result["status"] == "PARTIAL"
    assert any("MEASLES" in w for w in result["warnings"])


def test_excerpt_does_not_inherit_disease_from_another_sentence(books):
    req = request()
    h = hit(104, "수두와 유행성각결막염을 설명한다.\n휴업의 일반 원칙이다.")
    q = {**quote(h), "excerpt": "휴업의 일반 원칙이다."}
    result = records_to_tool_result({"actions": [], "quotes": [q]}, req, plan_action_task(req, books))
    assert result["status"] == "PARTIAL"


def test_table_quote_keeps_its_subject_without_inheriting_stale_section(books):
    h = hit(224, "| 감염병 | 조치 |\n| 유행성각결막염 | 개인위생수칙 준수 |", "COVID-19")
    assert evidence_diseases(h, books, excerpt="유행성각결막염 | 개인위생수칙 준수") == {"EKC"}


def test_quotes_do_not_displace_actions_and_actions_come_first(monkeypatch, books):
    # 11/05 3차 평가 퇴행(S-Q07): 행동+인용 총 상한에서 인용이 먼저 자리를 잡아 정답 행동이 잘렸다.
    direct = hit(104, CLOSURE)
    a = action(hit(105, "유행성각결막염 휴업 필요성을 검토한다."), diseases=["EKC"])
    _, result = retrieve_fixture(monkeypatch, books, request(top_k=1), [direct], [a])
    assert len(result["findings"]) == 2
    assert result["findings"][0]["action_ids"] and not result["findings"][1]["action_ids"]
    assert "권장하지 않음" in result["findings"][1]["claim"]
    assert [c["pdf_page"] for c in result["citations"]] == [105, 104]


def test_off_stage_action_is_ordered_after_matching_stage():
    plan = {"stages": ["RESPONSE_3"]}
    assert _off_stage({"stage": "RECOVERY"}, plan)
    assert not _off_stage({"stage": "RESPONSE_3"}, plan) and not _off_stage({"stage": "UNSPECIFIED"}, plan)
    assert not _off_stage({"stage": "RECOVERY"}, {"stages": None})


def test_role_context_does_not_require_every_task_to_repeat_operation(books):
    plan = plan_action_task(request("휴업 시 담임교사는 무엇을 하나요?"), books)
    assert plan["roles"] == ["HOMEROOM_TEACHER"]
    assert plan["operation_terms"] == []


def test_filter_can_reach_direct_rule_beyond_first_twelve_candidates(monkeypatch, books):
    # 후보 검색까지 k개로 자르면 질병명이 반복되는 증상표가 휴업 지침을 밀어낸다.
    candidates = [hit(180 + i, "유행성각결막염의 임상증상이다.", rank=i + 1) for i in range(15)]
    candidates.append(hit(104, CLOSURE, rank=16))
    monkeypatch.setattr(action_tool, "search_school_chunks",
                        lambda *a, **k: {"hits": candidates[:k["k"]], "mode": "hybrid"})
    monkeypatch.setattr(action_tool, "expand_school_actions", lambda *a, **k: [])
    monkeypatch.setattr(action_tool, "scan_actions", lambda *a, **k: [])
    monkeypatch.setattr(action_tool, "get_action_evidence", lambda *a, **k: [])
    req = request(top_k=2)
    plan = plan_action_task(req, books)
    records = retrieve_action_records(req, plan, {"driver": None, "books": books, "active_pages": {}})
    result = records_to_tool_result(records, req, plan)
    assert result["status"] == "OK"
    assert [c["pdf_page"] for c in result["citations"]] == [104]
    assert len(result["findings"]) <= 2


def test_direct_disease_rule_is_not_padded_with_general_or_scenario_evidence(monkeypatch, books):
    direct = hit(104, CLOSURE)
    general = hit(237, "학교장은 필요하면 기능상 휴업을 검토할 수 있다.")
    scenario = hit(40, "유행성각결막염 휴업 검토", "유행성각결막염 모의훈련 시나리오")
    records, result = retrieve_fixture(monkeypatch, books, request(), [direct, general],
                                      [action(scenario, diseases=["EKC"])])
    assert len(result["findings"]) == 1
    assert [c["pdf_page"] for c in result["citations"]] == [104]
    assert any(log["step"] == "prefer_direct_evidence" for log in records["log"])
