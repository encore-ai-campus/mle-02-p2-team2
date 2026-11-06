# 호흡기 골든 인용 대조 — 2026-11-06

기준: [respiratory.md](../../data/specs/golden/respiratory.md)의 질문 표와 Citation 검증표. 비교 키는 `document_id`와 PDF 뷰어 기준 `pdf_page`이며, 인용 배열의 1부터 시작하는 위치를 순위로 사용했다. R-Q3의 괄호 쪽은 보조 정답으로 분리했다. status는 각 저장 파일의 원래 값이다. R-Q2는 QueryOutput, 나머지는 ToolResult이므로 status의 의미가 서로 다르다. 문서·쪽 일치만 확인했으며 발췌의 정답성이나 실제 PDF 원문은 재검증하지 않았다.

| 질문ID | status | 정답 문서·쪽 | 결과 내 순위(없으면 없음) | 인용 수 | 1~3순위 문서·쪽 | 근거 파일 |
|---|---|---|---|---:|---|---|
| R-F1 | OK | 주: `kdca_flu_facility_2026` p6 | 주 p6: 1위 | 4 | 1. `kdca_flu_facility_2026` p6<br>2. `moe_school_manual_v3` p49<br>3. `moe_school_manual_v3` p224 | [R-F1_tool_result.json](../../tests/fixtures/respiratory/R-F1_tool_result.json) |
| R-F2 | NO_EVIDENCE | 주: `kdca_respiratory_2026` p109 | 주 p109: 없음 | 0 | 없음 | [R-F2_tool_result.json](../../tests/fixtures/respiratory/R-F2_tool_result.json) |
| R-F3 | OK | 주: `kdca_covid19_2026` p39 | 주 p39: 1위 | 3 | 1. `kdca_covid19_2026` p39<br>2. `kdca_respiratory_2026` p183<br>3. `kdca_covid19_2026` p43 | [R-F3_tool_result.json](../../tests/fixtures/respiratory/R-F3_tool_result.json) |
| R-F4 | OK | 주: `kdca_covid19_2026` p28 | 주 p28: 없음 | 8 | 1. `gyeonggi_school_guide_2025` p197<br>2. `gyeonggi_school_guide_2025` p239<br>3. `moe_school_manual_v3` p217 | [R-F4_tool_result.json](../../tests/fixtures/respiratory/R-F4_tool_result.json) |
| R-Q1 | OK | 주: `kdca_flu_facility_2026` p15 | 주 p15: 없음 | 8 | 1. `kdca_flu_facility_2026` p18<br>2. `gyeonggi_school_guide_2025` p195<br>3. `moe_school_manual_v3` p38 | [R-Q1_tool_result.json](../../tests/fixtures/respiratory/R-Q1_tool_result.json) |
| R-Q2 | ANSWERED | 주: `kdca_flu_facility_2026` p17<br>주: `kdca_flu_facility_2026` p18 | 주 p17: 2위<br>주 p18: 3위 | 3 | 1. `kdca_flu_facility_2026` p16<br>2. `kdca_flu_facility_2026` p17<br>3. `kdca_flu_facility_2026` p18 | [R-Q2_output.json](../../tests/fixtures/respiratory/R-Q2_output.json) |
| R-Q3 | OK | 주: `kdca_respiratory_2026` p118<br>보조: `kdca_respiratory_2026` p116 | 주 p118: 없음<br>보조 p116: 3위 | 5 | 1. `kdca_diagnosis_reporting_2026` p104<br>2. `gyeonggi_school_guide_2025` p181<br>3. `kdca_respiratory_2026` p116 | [R-Q3_tool_result.json](../../tests/fixtures/respiratory/R-Q3_tool_result.json) |
| R-Q4 | OK | 주: `kdca_respiratory_2026` p116 | 주 p116: 2위 | 8 | 1. `gyeonggi_school_guide_2025` p181<br>2. `kdca_respiratory_2026` p116<br>3. `kdca_diagnosis_reporting_2026` p104 | [R-Q4_tool_result.json](../../tests/fixtures/respiratory/R-Q4_tool_result.json) |
| R-T1 | OK | 주: `kdca_flu_facility_2026` p13 | 주 p13: 없음 | 8 | 1. `moe_school_manual_v3` p194<br>2. `kdca_flu_facility_2026` p15<br>3. `moe_school_manual_v3` p38 | [R-T1_tool_result.json](../../tests/fixtures/respiratory/R-T1_tool_result.json) |
| T01-MEN | OK | 정답 정보 없음 | 정답 정보 없음 | 3 | 1. `kdca_respiratory_2026` p90<br>2. `moe_school_manual_v3` p223<br>3. `gyeonggi_school_guide_2025` p159 | [T01-MEN_tool_result.json](../../tests/fixtures/respiratory/T01-MEN_tool_result.json) |
| T04 | OK | 정답 정보 없음 | 정답 정보 없음 | 9 | 1. `gyeonggi_school_guide_2025` p54<br>2. `gyeonggi_school_guide_2025` p166<br>3. `gyeonggi_school_guide_2025` p167 | [T04_tool_result.json](../../tests/fixtures/respiratory/T04_tool_result.json) |
| T04_live | OK | 정답 정보 없음 | 정답 정보 없음 | 9 | 1. `gyeonggi_school_guide_2025` p54<br>2. `gyeonggi_school_guide_2025` p166<br>3. `gyeonggi_school_guide_2025` p167 | [T04_live_tool_result.json](../../tests/fixtures/respiratory/T04_live_tool_result.json) |

실행: 아래 세 명령을 실행해 각각 `tests/fixtures/respiratory/`에 ToolInput과 ToolResult를 저장했다. 기존 가상환경을 사용하도록 `UV_NO_SYNC=1`, 임시 캐시를 사용하도록 `UV_CACHE_DIR=/private/tmp/codex-respiratory-uv`를 지정했다.

```text
uv run python -m rag.respiratory.disease_tool "인플루엔자로 진단받은 학생은 언제까지 등교하면 안 되나?" --save R-Q1
uv run python -m rag.respiratory.disease_tool "성홍열 집단발생 기준과 그때 학교가 할 일은?" --save R-Q4
uv run python -m rag.respiratory.disease_tool "인플루엔자가 유행하면 학교는 휴교해야 하나?" --save R-T1
```

- 실행 실패: 최초 샌드박스 실행 3건은 `ServiceUnavailable`; 샌드박스 밖 재실행 3건은 모두 `OK`를 반환하여 최종 실행 실패 없음.
- 파일이 없거나 열리지 않은 항목: 없음.
- 정답 정보 없는 질문: T01-MEN, T04, T04_live (지정된 호흡기 골든 기준 파일에 정답 문서·쪽 없음).
