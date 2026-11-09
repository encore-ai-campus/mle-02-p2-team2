from index.school.dictionaries import load_dictionaries
from index.school.exclusion_table_actions import build, disease_index, parse_chunk, to_action

HEADER = "| 감염병 | 임상 증상 | 감염 가능 기간 | 등교 중지(격리) 기간¹⁾²⁾ | 잠복기³⁾ |"


def chunk(row: str, cid: str = "moe_school_manual_v3:p0049:t01r01") -> dict:
    return {"chunk_id": cid, "document_id": "moe_school_manual_v3", "pdf_page": 49,
            "text": f"[표 3-2] 학교 내 빈발 감염병의 종류 및 특성\n{HEADER}\n{row}"}


def test_duration_cell_becomes_exclusion_action():
    c = parse_chunk(chunk("| 결핵 | 발열 | 약물 치료 시작 후 2주까지 | 약물 치료 시작 후<br>2주까지 | 수년 |"),
                    disease_index(load_dictionaries()))
    action = to_action(c, "moe_school_manual_v3:p0049:a02")
    assert c["disease_id"] == "TUBERCULOSIS" and action["timing_text"] == "약물 치료 시작 후 2주까지"
    assert action["action_text"] == "결핵 환자는 약물 치료 시작 후 2주까지 등교 중지(격리)한다."
    assert action["concept_id"] == "EXCLUDE_FROM_SCHOOL" and action["disease_ids"] == ["TUBERCULOSIS"]


def test_conditional_and_no_exclusion_cells_keep_source_wording():
    books = disease_index(load_dictionaries())
    flu = to_action(parse_chunk(chunk("| 인플루엔자 | 발열 | 1일 전부터 | 유행차단을 위한 등교 중지는 의미 없지만 환자상태에 따라 실시⁵⁾ | 1-4일 |"), books), "x:p0049:a03")
    assert flu["action_text"] == "인플루엔자 등교 중지(격리) 기간: 유행차단을 위한 등교 중지는 의미 없지만 환자상태에 따라 실시"
    ekc = to_action(parse_chunk(chunk("| 유행성 각결막염 | 충혈 | 14일 | 격리없이 개인 위생수칙을 철저히 지킬 것을 권장 | 5-7일 |"), books), "x:p0049:a04")
    assert ekc["concept_id"] is None and ekc["disease_ids"] == ["EKC"]


def test_unapproved_disease_rows_and_existing_concepts_are_skipped():
    rows = [chunk("| 풍진 | 발진 | 7일 | 발진 생긴 후 7일까지 | 2-3주 |", "moe_school_manual_v3:p0049:t01r09"),
            chunk("| 수두 | 수포 | 가피 | 모든 수포에 가피가 형성될 때까지 | 2-3주 |", "moe_school_manual_v3:p0049:t01r02")]
    linked = {"moe_school_manual_v3:p0049:t01r02": ["EXCLUDE_FROM_SCHOOL"]}
    actions, skipped = build(rows, {"moe_school_manual_v3:p0049": ["moe_school_manual_v3:p0049:a01"]}, linked, load_dictionaries())
    assert actions == [] and [s["disease_id"] for s in skipped] == ["VARICELLA"]
