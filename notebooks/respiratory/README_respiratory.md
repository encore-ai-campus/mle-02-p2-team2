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

- DB 점검 스냅샷 노드 합계 2,119: Document 9, Chunk 1,569, SchoolAction 490, ActionConcept 28, SchoolRole 12, Disease 11. (출처: [DB 점검 결과](db_check_result.json), 라벨별 합계)
- DB 점검 스냅샷 관계 합계 2,995: HAS_CHUNK 1,569, SUPPORTED_BY 490, DONE_BY 346, INSTANCE_OF 307, ABOUT 166, FOR_DISEASE 117. (출처: [DB 점검 결과](db_check_result.json), 타입별 합계)
- 허용 패턴 밖 관계 0; 문서별 SUPPORTED_BY 없는 행동, HAS_CHUNK 없는 청크, basis 없는 DONE_BY, 임베딩 없는 청크는 모두 0. (출처: [DB 점검 결과](db_check_result.json))
- 전체 `tests` 최신 실행은 347개 통과·10개 건너뜀. 건너뛴 검사는 학교·법령 담당의 로컬 원문 또는 검토 자료가 없는 경우다. (출처: [25개 업무 현황](업무현황_25개_20261106.md))
- 앞서 저장한 골든 인용 판정은 11질문 중 통과 4, 부분 통과 4, 실패 3이다. 이후 Tool 단독 재조회 결과는 조건과 순위가 일부 다르므로 별도로 확인한다. (출처: [골든셋 7장](../../data/specs/golden/respiratory.md#7-116-실행-결과), [11/6 진행 기록](오늘_진행_20261106.md))
- CrossDoc 실제 경로·원문과 메인 그래프의 단일 호출·조건부 추가 검색을 재확인했다. (출처: [11/6 진행 기록](오늘_진행_20261106.md))
- 11/7 보호자 연락 CrossDoc 단독 조회에서 교육부·경기도·인플루엔자 세 문서와 8개 경로가 나왔고, 확인한 DB 대조 오류는 없었다. (출처: [현재 CrossDoc 결과](crossdoc_current_20261107.json))
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
- FULLTEXT·VECTOR 후보를 RRF로 합친 뒤, 질병명과 핵심어가 있는 직접 후보를 조회하고 머리말·질병별 제외 문구를 적용해 합친다. (출처: [disease_tool.py](../../src/rag/respiratory/disease_tool.py))
- 중복·허용 문서·질병명 또는 실제 ABOUT 관계·핵심어·유효 인용을 확인하고 기본 `top_k=8`까지 반환한다. (출처: [disease_tool.py](../../src/rag/respiratory/disease_tool.py))

## 한계

- R-F2·R-F4의 정답 청크는 각각 5위다. 질병 목록이 반복되는 공통 머리말이 FULLTEXT 점수에 영향을 줄 가능성은 가설이며, 두 질문의 순위 원인으로 확정된 것은 아니다. (출처: [검색 수정 결과](golden_fix_20261106.md), [수정 전 추적](golden_trace_20261106.md))
- R-Q3의 p118은 머리말을 가린 본문에 질병명이 없고 해당 청크의 ABOUT 관계도 없어 현재 질병 필터의 한계가 있다. (출처: [수정 전 추적](golden_trace_20261106.md))
- R-Q2의 두 정답 쪽 중 p18은 2위, p17은 7위로 반환돼 p17의 검색 순위가 여전히 낮다. (출처: [11/6 진행 기록](오늘_진행_20261106.md))
- 수막구균 로컬 사전 ID에 대응하는 Disease 노드가 공통 DB에 없어 해당 원문은 질병 ID 없이 `PARTIAL`로 반환한다. (출처: [11/6 진행 기록](오늘_진행_20261106.md))
- 직접 후보는 chunk_id 순서로 훑고 필터를 통과한 최대 20개만 합친다. (출처: [disease_tool.py](../../src/rag/respiratory/disease_tool.py))
- 호흡기 로컬 적재 묶음의 DONE_BY 관계는 0개이며, 저장된 공동 DB 전체 DONE_BY 수와 구분해야 한다. (출처: 로컬 `data/interim/respiratory/bundle.json`, [DB 점검 결과](db_check_result.json))
- 호흡기 담당 문서의 표 행 청크는 적용되지 않았다. (출처: 로컬 `data/interim/respiratory/bundle.json`, [골든셋](../../data/specs/golden/respiratory.md))
- 코로나19 행동 추출은 보류 대상으로, 저장된 코로나19 문서의 SchoolAction 수는 0이다. (출처: [DB 점검 결과](db_check_result.json), 로컬 `data/interim/respiratory/steps_raw.jsonl`)
- 일반 Streamlit 화면의 기본 허용 문서 목록에 호흡기 담당 문서 3종이 빠져 있어 담당자 검토가 필요하다. (출처: [Streamlit 범위 확인](Streamlit_호흡기_범위_확인_20261107.md))
- 호흡기 원문은 일부개정판인데 manifest URL은 이전 판본을 가리킨다. (출처: [문서 판본 점검](기획배경_문서판본_20261107.md))

`data/interim/respiratory/`의 묶음·추출 산출물은 Git 제외 로컬 자료다.
