# ActionSearchTool·메인 LangGraph 인수 자료 (11/05, 주승우)

받는 사람: 송수림(Disease·CrossDoc), 유종혁(Legal·공통 계약 검증기). 계약 기준은 `공통계약_가이드_v2.0` §2~§13.

## 1. 실행 가능한 callable

| 대상 | import | 형태 | deps 소유권 |
|---|---|---|---|
| Action Tool | `from rag.school.action_tool import action_search` | `action_search(tool_input: ToolInput, deps: dict) -> ToolResult` | `deps["driver"]`는 **호출자 소유**. Tool은 닫지 않는다. `deps`: `driver`(필수), `database`, `embedder`(선택, 없으면 fulltext+Cypher), `books`(없으면 로드), `trace`(선택) |
| 메인 그래프 | `from rag.school.main_graph import run_main` | `run_main(query: QueryInput, deps: dict, registry: dict | None = None) -> QueryOutput` | 위와 같음. 실행 기록은 `deps["main_trace"]` |
| 계약 임시본 | `from rag.school.contract_v2_local import ...` | §9 타입 원문 + `validate_query_input` / `validate_tool_input` / `validate_tool_result(result, request, expected_tool)` / `validate_query_output` | 공통본이 오면 import만 교체 |
| DB 점검 | `from rag.school.capabilities import inspect_search_capabilities` | 읽기 전용 보고서 | — |

- 예외를 밖으로 던지지 않는다. DB 장애·계약 위반은 `status="ERROR"` ToolResult로 돌려준다.
- **다른 Tool을 메인에 연결하는 방법**(팀 규칙상 제 모듈이 여러분 폴더를 import하지 않으므로 호출하는 쪽에서 등록):

```python
from rag.school.main_graph import run_main, default_registry
registry = default_registry(deps) | {"legal_search": legal_search,            # ToolInput -> ToolResult
                                      "disease_search": lambda ti: disease_search(ti, deps)}
out = run_main(query, deps, registry)
```

  등록 이름은 승인된 4개만 허용한다(다른 이름은 ContractError). 등록 안 된 Tool이 선택되면 **호출하지 않고(0회)** 연결 오류로 남긴다. action_search로 대신 부르지 않는다.

## 2. 실제 ToolInput / ToolResult 예시 (실DB 실행, 일부 생략)

```json
{
 "tool_input": {
  "question": "학년부장은 능동감시에서 무엇을 하나요?",
  "task": "학교가 수행하는 행동, 역할, 단계 및 근거 찾기",
  "region": "GYEONGGI",
  "allowed_document_ids": [
   "moe_school_manual_v3",
   "gyeonggi_school_guide_2025"
  ],
  "top_k": 2
 },
 "tool_result": {
  "tool_name": "action_search",
  "status": "OK",
  "findings": [
   {
    "claim": "[경기도 가이드북 PDF 165쪽 · 대응 제3단계] 원문이 제시한 담당 예시: 학년부장 — 능동감시 체계 강화",
    "chunk_ids": [
     "gyeonggi_school_guide_2025:p0165:t02r08"
    ],
    "action_ids": [
     "gyeonggi_school_guide_2025:p0165:a28"
    ],
    "disease_ids": [],
    "concept_ids": [
     "ACTIVE_SURVEILLANCE"
    ]
   },
   {
    "claim": "[교육부 매뉴얼 PDF 65쪽 · 대응 제3단계] 원문이 제시한 담당 예시: 학년부장 — 담임교사로부터 전달받은 사항을 보건(담당)교사에게 보고한다. (시점: 매일 1교시 후)",
    "chunk_ids": [
     "moe_school_manual_v3:p0065:t01r03"
    ],
    "action_ids": [
     "moe_school_manual_v3:p0065:a03"
    ],
    "disease_ids": [],
    "concept_ids": [
     "CONTACT_HEALTH_TEACHER"
    ]
   }
  ],
  "citations": [
   {
    "document_id": "gyeonggi_school_guide_2025",
    "chunk_id": "gyeonggi_school_guide_2025:p0165:t02r08",
    "pdf_page": 165,
    "printed_page": 155,
    "excerpt": "학년부장|대응<br>제3단계|능동감시 체계 강화<br>- 매일 1교시 후 담임교사로부터 전달받은 사항을 보건(담당)교사에게 보고"
   },
   {
    "document_id": "moe_school_manual_v3",
    "chunk_id": "moe_school_manual_v3:p0065:t01r03",
    "pdf_page": 65,
    "printed_page": 51,
    "excerpt": "학년부장 | 매일 1교시 후 담임교사로부터 전달받은 사항을 보건(담당)교사에게 보고"
   }
  ],
  "graph_paths": [
   {
    "edges": [
     {
      "source_ref": "SchoolAction:gyeonggi_school_guide_2025:p0165:a28",
      "relationship_type": "SUPPORTED_BY",
      "target_ref": "Chunk:gyeonggi_school_guide_2025:p0165:t02r08"
     },
     {
      "source_ref": "Document:gyeonggi_school_guide_2025",
      "relationship_type": "HAS_CHUNK",
      "target_ref": "Chunk:gyeonggi_school_guide_2025:p0165:t02r08"
     },
     {
      "source_ref": "SchoolAction:gyeonggi_school_guide_2025:p0165:a28",
      "relationship_type": "DONE_BY",
      "target_ref": "SchoolRole:GRADE_HEAD"
     },
     {
      "source_ref": "SchoolAction:gyeonggi_school_guide_2025:p0165:a28",
      "relationship_type": "INSTANCE_OF",
      "target_ref": "ActionConcept:ACTIVE_SURVEILLANCE"
     }
    ]
   }
  ],
  "warnings": [
   "원문 인용 Finding은 검색된 원문을 그대로 옮긴 것이다. 해석·적용 판단은 포함하지 않는다.",
   "역할은 원문이 제시한 예시이며 학교 상황에 따라 조정할 수 있다(의무 배정 아님).",
   "…(생략)"
  ]
 }
}
```

같은 행동이 교육부(PDF 65쪽)·경기도(PDF 165쪽)에 따로 있다. Tool은 두 문서의 Finding·Citation을 합치지 않는다(같은 문구 정리는 메인의 답변 문장에서만).

### 실행·검증 상태 (11/05)

| 항목 | 결과 | 증빙 |
|---|---|---|
| Action 단독(T02 유형, 병명 없음) | OK, 실제 DONE_BY·SUPPORTED_BY·HAS_CHUNK 경로 + PDF 인용 | `notebooks/school/school_action_tool.ipynb` |
| 메인 1-Tool(M3) | 질문 → action_search 1회 → 실DB → QueryOutput, DB 대조·계약 검사 통과 | `notebooks/school/school_main_tool_call.ipynb` |
| T05·T08·T09·T10·T11, allowed=[], preferred⊄allowed, request_id | 통과 | 두 노트북 + `tests/rag/school/test_school_action_tool.py`, `test_school_main_graph.py` (school 테스트 154개 통과) |
| 질문셋 v1.1 40문항 | 라우팅 40/40, Tool 정답 인용 25/34 | `notebooks/school/평가결과/평가_20261105_기준선_v1.md` |
| T04·T06·T07 (CrossDoc·추가 호출 루프) | **미완(11/06)** | — |

## 3. 실DB 스키마·인덱스 지원 범위 (B01, 11/05 확인)

- 학교 문서 2개: 청크 1,139개(전부 임베딩, text-embedding-3-small 1536), 내 문서 SchoolAction 470개.
- 인덱스 `chunk_text`(fulltext), `chunk_embedding`(vector), `action_stage`, `action_scope` 모두 ONLINE.
- v3.1 속성: 모든 행동에 `stage` 있음(UNSPECIFIED 81), `DONE_BY.basis`는 전부 `EXAMPLE`(340). `DiseaseGroup` 노드 0개.
- 부족한 데이터: 본문 **문단 청크에서는 행동을 추출하지 않았다**(표 행만). 사전 미등록 역할(영양(교)사·부장급 교사 등) 행동 26개는 보류(팀 결정: 새 역할 추가 안 함).
- 경기도 PDF 54·56쪽 청크 8개·행동 32개는 선정쪽수 밖인데 DB에 남아 있다(11/05 결정). Action Tool은 manifest 선정쪽수로 걸러 쓰지 않는다. **다른 Tool도 이 쪽을 근거로 쓰지 않게** 확인 바람.

## 4. Cypher·GraphPath·Citation

- 역할 직접 조회(`search.get_role_actions`):

```cypher
MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)<-[:SUPPORTED_BY]-(a:SchoolAction)-[:DONE_BY]->(r:SchoolRole)
WHERE r.role_id IN $role_ids AND d.document_id IN $allowed AND ($stages IS NULL OR a.stage IN $stages)
WITH DISTINCT a, c, d
RETURN ... COLLECT { MATCH (a)-[r:DONE_BY]->(role) RETURN {role_id, name, basis} } AS roles, ...
```

- 그 밖의 경로: 청크 검색(hybrid) → `expand_school_actions`, 허용 문서 행동 전수 조회 → 관련도 점수, 선택한 행동은 `get_action_evidence`로 근거 청크를 **다시** 확인(여러 근거 청크 보존).
- GraphPath는 실제 조회한 관계만 저장 방향 그대로 만든다(`SchoolAction-SUPPORTED_BY->Chunk`, `Document-HAS_CHUNK->Chunk`, `DONE_BY`(basis 있는 것만), `INSTANCE_OF`, `FOR_DISEASE`). `check_graph_paths`가 실DB에서 끝점·관계·방향·허용 문서를 다시 검사한다(가짜·반대 방향·허용 밖 경로 거절 확인).
- 참조 문자열은 **첫 ':'만** 라벨 구분자다(`split_ref`).

## 5. 다음 Tool·담당자가 확인할 것

1. **`[미해결]` 경고 접두어**: Tool이 다른 Tool의 확인이 필요한 부분을 `warnings`에 `[미해결] …`로 남기면 메인이 PARTIAL로 유지하고 11/06 추가 호출 판단에 쓴다. 다른 Tool도 같은 접두어를 쓰면 바로 연결된다(계약 필드 추가 없음). **3인 합의 필요.**
2. **유종혁 님**: `src/rag/prevention_legal/tool.py`의 로컬 `GraphPath`가 `nodes`/`relations`로 정의돼 있다. 계약 §6·§9는 `edges: [{source_ref, relationship_type, target_ref}]`다. 지금은 `graph_paths=[]`라 동작에는 문제없지만 공통 타입 확정 시 맞춰야 한다. 공통 검증기가 오면 제 `contract_v2_local`과 차이를 비교하겠다.
3. **송수림 님(CrossDoc)**: 기존 `rag.school.search.compare_action_sources(driver, concept_ids, allowed, *, exclude_action_ids=None, limit=50, database=None)`는 같은 `ActionConcept`에 속한 다른 출처 행동을 `ACTION_FIELDS`(행동·문서·청크·역할+basis·개념·질병)로 돌려준다. allowed는 모든 행에 적용된다. CrossDoc 경로 Cypher의 출발점으로 쓸 수 있다(오늘 Action Tool은 비교를 하지 않고 `[미해결] 문서별 비교는 crossdoc_graph_search 몫`을 남긴다).
4. 메인 분류기는 사전·단서 기반 규칙이다(진단·대행·인사 분기 포함). 애매한 질문을 LLM으로 보강하는 것은 11/06 이후 과제.
5. 오늘 메인은 Tool 1회 후 종료한다. 재진입·총 3회·동일 입력 반복 방지는 11/06에 구현한다(State에 `tool_results`·`unresolved`·`search_rounds` 준비됨).

## 6. 11/05 검색 적합성 보완

오늘의 M3 범위에서 Action Tool의 선별과 상태를 보완했다. 최종 답변은 기존 Finding 나열 형식을 유지하며, 새 LLM 답변 생성이나 추가 Tool 재진입은 도입하지 않았다.

- `relevance.py`: 단계 힌트를 읽을 때만 사전의 질병명을 마스킹한다. 유행성각결막염·유행성이하선염만으로 제3단계를 고르지 않으며, 별도로 쓴 '유행하면'과 명시 단계는 보존한다. 원래 질문은 바꾸지 않는다.
- 질병 적합성: 다른 질병으로 명시된 행동·본문 절을 제외한다. 질병별 표는 해당 행의 질병명을 앞 절 제목보다 우선한다. 사전 미등록 질병의 표 행도 특정 질병에 자동 적용하지 않으며, '눈병' 같은 검수용 별칭을 EKC로 연결하지 않는다.
- 조치 적합성: 역할 수행 목록을 묻지 않고 휴업·휴교·등교중지·소독·환기·능동감시를 직접 물으면 해당 표현이 있는 행동/근거를 선별한다. 역할 질문은 조치가 배경일 수 있으므로 이 제한을 적용하지 않는다.
- 후보와 출력 상한 분리: 조치가 명확한 질문은 `min(64, max(32, top_k * 4))`개 후보를 확보한다. 적합성 검사 → 기존 점수 정렬/역할·문서 분산 → 총 Finding `top_k` 상한 순이다. 점수는 확률이 아니며 기존 행동 점수 문턱 4를 높이지 않았다. 빈 자리를 채우지 않는다.
- 특정 질병·조치의 본문 직접 근거가 있으면 일반 지침과 모의훈련 근거로 결과를 채우지 않는다. 시나리오를 명시적으로 요청한 질문은 이 우선 규칙에서 제외한다.
- 일반 지침만 있거나 질문한 질병 중 일부의 적용 근거가 없으면 `[미해결]` 경고와 `PARTIAL`을 반환한다. `COMMON`은 특정 질병의 적용 근거가 아니다. DB 검증은 기존대로 실제 노드·관계·발췌를 대조한다.
- 내부 trace의 `relevance_filter`에 제외 ID·이유를 기록하고, `prefer_direct_evidence`에 제외한 배경 근거 수를 기록한다. 계약의 ToolResult/QueryOutput 필드는 추가하지 않았다.

실제 공통 Neo4j의 하이브리드 검색으로 확인한 결과:

| 질문 | 최종 상태 | Finding 수 | 단계 힌트 |
|---|---|---|---|
| 유행성각결막염 걸렸을 때도 전파차단 위한 휴업 해야하나요? | ANSWERED | 2 | 없음 |
| 유행성이하선염의 휴업 기준은? | ANSWERED | 2 | 없음 |
| 유행성각결막염이 유행하면 휴업해야 하나요? | ANSWERED | 2 | RESPONSE_3 |
| 유증상 학생 발견 시 담임·보건교사의 확인 사항 | ANSWERED | 8 | RESPONSE_1 |

네 질문 모두 Tool 1회 종료와 DB 인용·관계 검증을 통과했다. 첫 질문에는 교육부 PDF 104쪽·경기도 PDF 236쪽의 비권장 원칙 및 방역당국의 역학조사 후 권고 시 실시한다는 예외가 남았다. DB 쓰기는 하지 않았다.

`school_main_tool_call.ipynb`의 직접 질문 셀만 새 코드로 독립 재실행하여 출력을 갱신했다. 기존 커널에서도 수정 모듈을 반영하도록 해당 셀에 reload를 추가했다. 다른 셀의 출력과 40문항 평가는 이전 실행 기록이며 이번 보완으로 재평가한 결과가 아니다. 실DB 증빙은 Git 제외 `data/interim/school/relevance_check_20261105_hybrid.json`에 저장한다.

이 검사는 명시 표기·절·행을 사용하는 결정적 선별이며 자연어의 모든 적용 조건을 판정하는 의미 검증기는 아니다. 사전 미등록 표현, 잘못 추출된 절 정보, 검색 후보 밖 근거는 추가 검수 대상으로 남는다.
