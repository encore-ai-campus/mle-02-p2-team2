# 호흡기 담당 작업 안내

## 담당과 문서

- 담당: 송수림. (출처: [골든셋](../../data/specs/golden/respiratory.md))
- 호흡기감염병 관리지침: `kdca_respiratory_2026`. (출처: [DB 점검 결과](db_check_result.json))
- 인플루엔자 관리지침(어린이집·유치원·학교용): `kdca_flu_facility_2026`. (출처: [DB 점검 결과](db_check_result.json))
- 코로나바이러스감염증-19 관리지침: `kdca_covid19_2026`. (출처: [DB 점검 결과](db_check_result.json))

## 만든 것

- 질병 사실·원문 검색: [`disease_search`](../../src/rag/respiratory/disease_tool.py).
- 문서별 행동·담당 비교: [`crossdoc_graph_search`](../../src/rag/respiratory/crossdoc_tool.py).
- 공동 DB 읽기 전용 점검 및 결과 저장: [`db_check.py`](db_check.py).
- 문서 간 경로 시각화 질의: [`visual_queries.cypher`](visual_queries.cypher).

## 실행 명령

저장소 루트에서 실행한다. 검색과 점검은 DB를 읽으며, `db_check.py`는 로컬 `db_check_result.json`을 덮어쓴다.

```bash
uv run python -m rag.respiratory.disease_tool "인플루엔자 잠복기는?"
uv run python -m rag.respiratory.crossdoc_tool "보호자 연락은 문서마다 누가 하나요?" --concept CONTACT_GUARDIAN
uv run python notebooks/respiratory/db_check.py
uv run pytest tests/rag/respiratory -q
```

## 저장된 결과

- 담당 3문서의 조각(Chunk)은 389개(호흡기 286·인플루엔자 30·코로나19 73)로, 빈 페이지 표시 8개 정리 후 수치다. 중복 검토 문서는 정리 전 기록이다. (출처: [DB 중복 검토](DB_중복_검토_20261106.md), [DB 점검 결과](db_check_result.json))
- 담당 3문서의 행동(SchoolAction)은 15개(호흡기 2·인플루엔자 13·코로나19 0)이며 성홍열 p116 등교중지 행동 추가를 포함한다. (출처: [DB 점검 결과](db_check_result.json))
- DB 점검 스냅샷 노드 합계 2,255: Document 10, Chunk 1,735, SchoolAction 459, ActionConcept 28, SchoolRole 12, Disease 11. (출처: [DB 점검 결과](db_check_result.json))
- DB 점검 스냅샷 관계 합계 3,076: HAS_CHUNK 1,735, SUPPORTED_BY 459, DONE_BY 312, INSTANCE_OF 290, ABOUT 166, FOR_DISEASE 114. (출처: [DB 점검 결과](db_check_result.json))
- 점검 항목의 위반은 모두 0: 허용 패턴 밖 관계, SUPPORTED_BY 없는 행동, HAS_CHUNK 없는 청크, basis 없는 DONE_BY, 임베딩 없는 청크. (출처: [DB 점검 결과](db_check_result.json))
- 전체 `tests` 실행 결과는 421 passed·11 skipped이며, 요청 범위의 ruff도 통과했다. (출처: [F3 골든 비교 결과](F3_golden_compare_20261108.json)의 `validation`)
- 골든 11문항 회귀 검증 결과는 통과 5·부분 통과 4·실패 2, F3 전후 판정 하락은 0이다. 이미 사용한 질문셋이며 독립 검증은 하지 않았다. (출처: [F3 골든 비교 결과](F3_golden_compare_20261108.json))
- 성홍열 등교중지 질문의 메인 최종 답변은 항생제 치료 시작 후 최소 24시간까지 등교중지하는 기준을 포함했고, 호흡기 p294·p116을 근거로 인용했다. 한 질문의 실행 결과이며 F3 전후 메인 답변 비교는 아니다. (출처: [R-Q3 메인 검증 결과](R-Q3_main_check_20261108.json))
- CrossDoc 실제 경로·원문과 메인 그래프의 단일 호출·조건부 추가 검색을 재확인했다. (출처: [11/6 진행 기록](오늘_진행_20261106.md))
- 11/8 보호자 연락 문서 비교 검색은 OK이며 경기도·인플루엔자·교육부 3문서를 인용하고, 인용·경로는 각각 8개다. (출처: [T04 문서 비교 결과](../../tests/fixtures/respiratory/T04_1108_tool_result.json))
- DB 수치는 저장된 스냅샷이며 현재 DB와 같다고 가정하지 않는다. (출처: [DB 점검 결과](db_check_result.json))

## 이어서 볼 자료

- 전체 25개 항목의 근거와 남은 단계: [업무 현황](업무현황_25개_20261106.md).
- 처음 보는 사람을 위한 전처리·추출·DB·검색 설명: [호흡기 코드 흐름](호흡기_코드_흐름_설명.md).
- 예방·법령 문서와 실제 관계를 포함한 설계도: [ERD v0.2](Neo4j_ERD_v0.2.md).
- 호흡기 로컬 패키지와 실제 DB 대조: [정규화·적재 확인](정규화_적재_대조_20261107.md).
- DB 로컬 내보내기와 복구 시험 한계: [백업 기록](DB_백업_20261107.md). 압축 파일에는 원문·임베딩이 있어 로컬 전용으로 보관한다.
- 표 행 후보·프롬프트 v1의 검수 및 적용 상태: [표 행 후보](표_행_후보_20261106.md).
- 원문 판본·manifest 불일치: [담당 문서 정정안](manifest_담당문서_정정안_20261107.md).
- 질병 질문의 별칭 구별: [질병명·별칭 점검](질병명_별칭_검토_20261107.md).
- Streamlit 기본 문서 범위와 실제 화면 검증 제한: [화면 범위 확인](Streamlit_호흡기_범위_확인_20261107.md).

## disease_search 처리 순서

- 질병명·별칭과 핵심어를 인식하고 허용 문서 범위를 정한다. (출처: [disease_tool.py](../../src/rag/respiratory/disease_tool.py))
- FULLTEXT·VECTOR 후보를 RRF로 합친 뒤, 머리말·제외 문구를 가린 본문에 질병명과 핵심어가 있는 DIRECT 후보를 합친다. (출처: [F3 골든 비교 결과](F3_golden_compare_20261108.json))
- F1: DIRECT 전체 페이지를 조회해 핵심어·핵심 구문 일치도 상위 20개를 선택하고 chunk_id는 동점 처리에만 쓴다. 200개 초과 시 경고하며 상위 200개를 보관하고 조회는 계속한다. (출처: [F1 골든 비교 결과](F1_golden_compare_20261108.json))
- F3: 질문 핵심어에 진단 또는 기준과 행위 단어(현재 코드에 고정된 10개)가 함께 있으면, 본문 행위까지 맞는 DIRECT 후보의 선택 점수에 1, RRF 점수에 1/61을 추가한다. 기존 SCOPED 후보·가산점은 유지한다. (출처: [F3 골든 비교 결과](F3_golden_compare_20261108.json))
- 중복·허용 문서·질병명 또는 실제 ABOUT 관계·핵심어·유효 인용을 확인하고 기본 `top_k=8`까지 반환한다. (출처: [disease_tool.py](../../src/rag/respiratory/disease_tool.py))

## 한계

- R-F2 정답은 6위로 실패, R-F4 정답은 3위로 부분 통과다. (출처: [F3 골든 비교 결과](F3_golden_compare_20261108.json))
- R-Q3 단독 검색은 p116이 3위로 부분 통과이며, p118·p121은 인용되지 않았다. 메인 최종 답변의 확인 결과와 구분한다. (출처: [F3 골든 비교 결과](F3_golden_compare_20261108.json), [R-Q3 메인 검증 결과](R-Q3_main_check_20261108.json))
- R-Q2는 p18이 1위·p17이 4위이며 최고 정답 순위 기준으로 통과다. (출처: [F3 골든 비교 결과](F3_golden_compare_20261108.json))
- 수막구균 질문의 원문 p90은 1위로 골든 통과지만, 대응 Disease 노드가 없어 Tool은 질병 ID 없이 PARTIAL을 반환한다. (출처: [F3 골든 비교 결과](F3_golden_compare_20261108.json))
- F1·F3는 기존 골든 질문에 노출된 상태의 개선으로, 회귀 검증의 판정 하락 0이 새 질문이나 최종 답변 품질 전체를 보장하지는 않는다. (출처: [F1 골든 비교 결과](F1_golden_compare_20261108.json), [F3 골든 비교 결과](F3_golden_compare_20261108.json))
- 개선 후보로 p116 발췌 위치 선정과 행동 단어 사전화(귀가·귀가조치 포함)를 남겼다. 코드에 적용하지 않았으며, 이번 메인은 p116 본문 추가 구간을 읽어 24시간 기준을 보완했다. (출처: [R-Q3 메인 검증 결과](R-Q3_main_check_20261108.json)의 `proposed_changes_only`·`p116_excerpt_diagnosis`)
- 호흡기 로컬 적재 묶음의 DONE_BY 관계는 0개이며, 저장된 공동 DB 전체 DONE_BY 수와 구분해야 한다. (출처: 로컬 [bundle.json](../../data/interim/respiratory/bundle.json), [DB 점검 결과](db_check_result.json))
- 호흡기 담당 문서의 표 행 청크는 적용되지 않았다. (출처: 로컬 [bundle.json](../../data/interim/respiratory/bundle.json), [골든셋](../../data/specs/golden/respiratory.md))
- 코로나19 p28의 프롬프트 v1 추출은 후보 0건이다. 학교 주체가 명시되지 않았으며, 코로나19 문서의 적재된 행동도 0개다. (출처: [코로나19 p28 v1 추출 결과](covid_p0028_v1_extract_20261108.json), [DB 점검 결과](db_check_result.json))
- 이번 메인 검증은 호흡기 담당 3문서를 포함한 DB 전체 10문서를 허용했다. 성홍열 질문 1건의 실행이며 Streamlit 화면 전체의 독립 검증은 아니다. (출처: [R-Q3 메인 검증 결과](R-Q3_main_check_20261108.json))
- 호흡기 원문은 일부개정판인데 manifest URL은 이전 판본을 가리킨다. (출처: [문서 판본 점검](기획배경_문서판본_20261107.md))

`data/interim/respiratory/`의 묶음·추출 산출물은 Git 제외 로컬 자료다.
