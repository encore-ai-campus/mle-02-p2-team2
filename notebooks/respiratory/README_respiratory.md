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

## 제출 증빙: 전처리·추출·정규화

### M1. 실제 전처리 전후 예시

시설별 인플루엔자 PDF 17쪽(인쇄 13쪽)의 변환 Markdown을 사용했다. `clean_page()` 재계산 결과가 저장된 청크의 `text`와 같은 것을 읽기 전용으로 확인했다. 이 쪽의 이미지 표시 1개와 불필요한 연속 빈 줄을 정리하고, 원문·문서 ID·청크 ID·두 종류의 쪽수를 보존한다. 본문 머리말을 검색에서 가리는 처리는 전처리의 표시 정리와 별개다. (출처: [전처리 코드](../../src/preprocess/respiratory/md_to_blocks.py), [변환 원문 — 로컬 자료](<../../data/raw/respiratory/2026 시설별 인플루엔자 관리 지침(어린이집 유치원 학교용) 복사본.md>), [저장 청크 — 로컬 자료](../../data/interim/respiratory/blocks.jsonl))

전처리 전: 해당 쪽 서두의 연속 원문 일부.

```text


2. 시설에서의 인플루엔자 예방 및 관리

#### **바. 대상별 역할**


**1** **학부모**


① 유행 전까지 자녀의 예방접종 실시


② 자녀의 개인위생 관련 지도


③ 유행 시기에 자녀의 외출 시 사람이 많이 모이는 장소의 방문을 삼가며, 특히 발열 및 기침



```

전처리 후: 실제 저장 JSON의 선택 필드이며, `text`는 앞부분만 발췌했다. 전체 청크를 줄인 새 데이터가 아니라 설명용 표시다.

```json
{
  "document_id": "kdca_flu_facility_2026",
  "chunk_id": "kdca_flu_facility_2026:p0017:b01",
  "pdf_page": 17,
  "printed_page": 13,
  "section": "바. 대상별 역할",
  "text": "2. 시설에서의 인플루엔자 예방 및 관리\n\n#### **바. 대상별 역할**\n\n**1** **학부모**\n\n① 유행 전까지 자녀의 예방접종 실시\n\n② 자녀의 개인위생 관련 지도\n\n③ 유행 시기에 자녀의 "
}
```

표 행 청크(11/8):[table_rows.py](../../src/preprocess/respiratory/table_rows.py)로 본문표 작업본·코로나19 변환본을 행 단위 청크 `{doc}:p0000:t01r01` 1,116개로 나눴다(호흡기 983·인플루엔자 9·코로나19 124,파싱 경고 0). 선정 쪽 19행(호흡기 p113·p117 6행,코로나19 p39 13행)은 원본 작업본과 행 원문이 일치함을 확인했다([검수표](table_rows_검수표_20261108.md),[요약](table_rows_summary_20261108.json)). 인플루엔자 표는 p9~11에만 있어 선정 쪽 행은 0이다. DB 적재(`load.py --table-rows`,새 청크·HAS_CHUNK만 추가,기존 노드 변경 0):①OK 16행 적재 시 골든 회귀에서 R-Q4(성홍열 집단발생 p116)가 3→6위로 하락해 추가분 16개를 삭제·복구(차이 0). 원인 추정:p113 성홍열 신고 행이 p116보다 위로 올라옴(추정,미검증)→p113 3행은 HOLD. ②코로나19 p39 13행만 다시 적재:Chunk·HAS_CHUNK +13,임베딩 13,verify 차이 0,DB 노드 2,268·관계 3,089·위반 0,골든 11문항 통과 5·부분 4·실패 2·하락 0.

### M3. 실제 사용 프롬프트와 실행 코드

- [v0 전체 프롬프트](../../src/index/respiratory/prompts/v0_school_action.md): 기존 행동 추출에 사용했다. 아래 p17:a04의 저장된 `review.prompt_version`도 `v0`이다. (출처: [추출 원본 — 로컬 자료](../../data/interim/respiratory/steps_raw.jsonl))
- [v1 전체 프롬프트](../../src/index/respiratory/prompts/v1_school_action.md): 표 행·머리글·근거 인용 규칙을 보강한 버전이다. 코로나19 p28에서는 실제 실행해 학교 행동 후보 0건을 확인했으며, 표 행 전체의 실제 재추출·DB 전환 완료를 뜻하지 않는다. (출처: [p28 실행 결과](covid_p0028_v1_extract_20261108.json), [표 행 후보·검증 기록](표_행_후보_20261106.md))
- [추출 실행 코드](../../src/index/respiratory/extractor.py): 문서·쪽수로 입력 선택 → LLM 호출 → JSON 파싱·허용값·원문 인용 검증 → 검수 전 후보 저장으로 연결한다. (출처: [전처리부터 검색까지 코드 설명](호흡기_코드_흐름_설명.md))

### M5. 대표 ID 연결과 실제 정규화 전후 사례

질병 표기에는 사전의 대표명·별칭을 사용한다. 동일 ID로 연결되는 이름과 다른 질병의 오병합 방지를 로컬 코드로 확인했다. 아래 표는 사전·질병명 인식 결과이며, `normalize.py`가 원문 이름을 직접 바꾼 저장 행이라고 주장하지 않는다. (출처: [질병 사전](../../src/index/respiratory/dictionaries/diseases.json), [공통 사전·별칭 매핑 코드](../../src/index/school/dictionaries/__init__.py), [질병명 인식·제외 코드](../../src/rag/respiratory/disease_tool.py))

| 입력 표기 | 대표 ID 또는 처리 | 의미 |
|---|---|---|
| 인플루엔자 | INFLUENZA | 대표 질병 ID |
| 독감 | INFLUENZA | 같은 질병의 별칭을 같은 ID로 연결 |
| 조류인플루엔자·파라인플루엔자 | 일반 INFLUENZA로 연결하지 않음 | 별도 질환을 일반 인플루엔자로 오병합하지 않음 |

저장된 행동 `kdca_flu_facility_2026:p0017:a04`는 사람의 원문 검토 결정에 따라 미확정 행동 개념을 기존 `CONTACT_GUARDIAN`에 연결했다. 행동 문장과 `evidence_quote`는 전후 동일하며, 역할이 특정되지 않아 `done_by=[]`도 유지했다. 현재 구현은 폐쇄형 사전·허용 ID와 명시적 검수 결정을 사용하며, 자유형 표기를 자동으로 모두 통합하는 Entity Resolution을 구현한 것은 아니다. (출처: [정규화 코드·검토 결정](../../src/index/respiratory/normalize.py), [추출 전 행 — 로컬 자료](../../data/interim/respiratory/steps_raw.jsonl), [정규화 후 행 — 로컬 자료](../../data/interim/respiratory/steps_normalized.jsonl))

정규화 전·후의 선택 필드:

```json
{
  "before": {
    "action_id": "kdca_flu_facility_2026:p0017:a04",
    "instance_of": "NEW_CONCEPT_CANDIDATE",
    "for_disease": [
      "INFLUENZA"
    ],
    "supported_by": [
      "kdca_flu_facility_2026:p0017:b01"
    ],
    "review": {
      "status": "PENDING"
    }
  },
  "after": {
    "action_id": "kdca_flu_facility_2026:p0017:a04",
    "instance_of": "CONTACT_GUARDIAN",
    "for_disease": [
      "INFLUENZA"
    ],
    "supported_by": [
      "kdca_flu_facility_2026:p0017:b01"
    ],
    "review": {
      "status": "APPROVED"
    }
  }
}
```

정규화된 ID가 관계의 끝점에도 사용되는 것을 저장된 번들에서 확인했다. 아래는 실제 관계를 표시한 것이다. (출처: [번들 — 로컬 자료](../../data/interim/respiratory/bundle.json), [매핑·적재 코드](../../src/index/respiratory/load.py))

```text
SchoolAction:kdca_flu_facility_2026:p0017:a04 -[:INSTANCE_OF]-> ActionConcept:CONTACT_GUARDIAN
SchoolAction:kdca_flu_facility_2026:p0017:a04 -[:FOR_DISEASE]-> Disease:INFLUENZA
SchoolAction:kdca_flu_facility_2026:p0017:a04 -[:SUPPORTED_BY]-> Chunk:kdca_flu_facility_2026:p0017:b01
```

스키마·그래프 제출 증빙: [ERD·관계 방향 설명](Neo4j_ERD_v0.2.md), [Cypher 질의](visual_queries.cypher), [기존 성홍열 그래프 캡처](captures/scarlet_fever.png), [기존 문서 횡단 그래프 캡처](captures/crossdoc.png). 캡처는 이전 실행 기록이며 최신 전체 수치는 [DB 점검 결과](db_check_result.json)를 따른다.


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


## Text2Cypher 조회(11/9 추가)

초기 CLI 버전(src/rag/respiratory/text2cypher_tool.py).
시험 질문이 프롬프트 예시와 같아 생성 능력 증명은 아님.
시연·평가는 아래 'Text2Cypher 그래프 시연' 절을 따른다.


## Text2Cypher 그래프 시연(11/9)

[질문 입력 노트북](text2cypher_demo.ipynb)에서 실제 Aura 구조·값 사전을 펼쳐 보고 자유 질문 → LLM 생성 → EXPLAIN 검사 → 읽기 실행 순서로 진행한다. Path·Node·Relationship을 보존하고, 검증한 같은 쿼리를 Aura 조회 화면에 복사해 그래프를 확인하는 개발자용 시연이다. 기존 터미널 Tool·메인 LangGraph·DB 스키마·데이터는 바꾸지 않았다. ([사용법](Text2Cypher_시연_사용법.md), [시연 부품](text2cypher_demo.py), [실제 스키마](text2cypher_schema.json))

- 실제 구조 6종·관계 방향 6종, 문서 10·질병 11·역할 12·행동 개념 28개의 값 사전을 수집했다. 전체 원문·embedding·비밀 접속 정보는 LLM에 보내지 않는다.
- CALL 읽기 서브쿼리·UNION은 허용하고 프로시저·외부 접근·쓰기·관리 구문은 거부한다. EXPLAIN r 판정과 실행 직전 재검사를 적용한다. LIMIT을 자동으로 붙여 의미를 바꾸지 않으며 연결·트랜잭션 10초, 표시 50행·100노드·150관계, 가변 경로 최대 3홉으로 제한한다.
- 사용자 요청에 따라 기존 계정을 사용했다. **DB 계정 자체가 읽기 전용인 것은 아니며**, 프로그램에서 조회만 허용한다. 읽기 전용 계정 요건과의 차이를 [스키마 계정 확인](text2cypher_schema.json)에 기록했다.
- 실제 평가 8문항: 조회 6/6 기준 결과 일치·지원 밖/쓰기 요청 거부 2/2. 예시 2개와 새 질문 6개를 구분하며, 새 질문 6개 중 2개는 거부 시험이다. 기존 3개 고정 조회 결과도 10·6·8행으로 같았다. ([질문·생성문·실행문·ID/집계·지연 기록](text2cypher_eval.jsonl), [평가 요약](text2cypher_eval_summary.json))
- 평가 전후 DB 노드 2,271·관계 3,098로 개수 변화 0. 이는 11/9 실행 시점 수치이며 이전 11/8 스냅샷과 구분한다.
- 노트북 코드 셀 8개 오류 0, 입력 UI 콜백 실제 실행 8행. 호흡기 테스트 `89 passed in 1.43s`, ruff 통과. 패키지 추가 설치·Git 실행은 없었다.
- **Aura 화면 대조 확인 완료**(11/9 10:01, 송수림 직접 실행): 같은 프로젝트 인스턴스의 Query 화면에서 E01을 실행해 문서 3개·SchoolAction 8개(교육부 4·경기 3·인플루엔자 1)·ActionConcept 1개(개별 보호자 연락=CONTACT_GUARDIAN)와 `Document-HAS_CHUNK->Chunk, SchoolAction-SUPPORTED_BY->Chunk, SchoolAction-INSTANCE_OF->ActionConcept` 방향을 그래프로 확인했다. 표의 행 수를 직접 본 것은 아니며, 그래프의 SchoolAction 노드 8개·문서 3개로 Python 결과 8행·3문서와 같음을 확인했다. [캡처](captures/t2c_contact_guardian.png)
- 소수 질문의 단회 평가이며 별도 사람 판독은 미실시다. 생성 Cypher는 틀릴 수 있고 결과는 Citation을 대신하지 않는다. `neo4j-graphrag` 미설치 환경에서 수업의 생성·검증 흐름을 분리 적용했다.

```bash
# 기존 환경으로 평가 재실행: 새 LLM 호출이 발생하며 DB는 읽기만 한다.
uv run --no-sync python notebooks/respiratory/text2cypher_evaluate.py
```
