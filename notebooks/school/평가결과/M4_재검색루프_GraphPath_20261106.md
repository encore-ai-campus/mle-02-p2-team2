# M4 — 근거 평가·조건부 재검색 루프 + 실DB GraphPath (2026-11-06)

증빙 노트북: [school_main_m4_loop.ipynb](../school_main_m4_loop.ipynb) · 원시 로그: `data/interim/school/m4_loop_logs_20261106.json`, 골든 회귀 `eval_main_m4_alltools.json` (Git 제외)

## 기준 (일정표 M4)

"실제 GraphPath 1개 + 필요시 추가 검색·정상 종료 1개". 주승우 제출물은 "메인 Graph의 **1 Tool 종료 + 추가 Tool 호출** 각각 실행 로그".

## 구현 (`src/rag/school/main_graph.py`)

| 항목 | 규칙 |
|---|---|
| 종료 | 근거가 충분하면(ANSWERED) 추가 호출 없이 끝낸다 |
| 재검색 신호 | ① 질문 분류의 보조 의도 ② Tool 경고 `[미해결] … <tool 이름>`. 신호 없이 다른 Tool을 부르지 않는다 |
| 한도·반복 | 총 3회(`MAX_TOOL_CALLS`), 같은 Tool은 한 번만 |
| 다음 Tool 입력 | 같은 질문 + task에 "앞선 호출에서 남은 부분", 앞선 Finding의 `Disease:`·`ActionConcept:` ID를 `context_entity_ids`로 전달 |
| 해소 판정 | 다른 Tool을 가리키던 `[미해결]`은 그 Tool이 OK면 해소. 실패하면 "호출했지만 근거를 찾지 못했다" 한 줄로 정리 |
| 주 의도 실패 | 주 의도 Tool이 근거를 못 냈는데 다른 Tool만 답하면 PARTIAL + "질문의 주 의도에서 근거를 찾지 못했다" |
| 남은 확인 사항 | 한도 초과·미연결·실패한 확인을 답변의 '참고'에 남긴다 |

분류 보완:
- 법령·질병 질문 안에 학교 행동을 함께 묻는 표현("무엇을 하", "조치", "역할", "안내하나요" 등)이 있으면 ACTION을 보조 의도로 둔다. 역할 이름만으로는 넣지 않는다("학교장이 신고해야"의 학교장은 신고 주체).
- 행동 질문에 병명과 좁은 질병 사실 낱말(잠복기·전파경로·합병증 등)이 함께 있으면 DISEASE를 보조 의도로 둔다. '전파 차단 휴업'의 '전파'처럼 넓은 낱말로는 넣지 않는다.
- "신고도 해야 하나요"를 법령 단서로 인식한다.

가짜 Tool 테스트 12개를 추가했다. 충분 시 1회 종료, 보조 의도 2회와 context 전달, `[미해결]` 신호와 해소, 3회 한도와 보고, 같은 Tool 재호출 없음, 두 번째 Tool 실패, 미연결 보조, 주 의도 근거 없음, 중복 보고 방지를 검사한다. school 테스트 **210개 통과**, ruff 통과.

## 실DB 결과 (Tool 4개 연결)

| 사례 | 호출 순서 | 호출 | 최종 | Finding / Citation | DB 재대조 |
|---|---|---|---|---|---|
| A. 근거 충분 | action | 1 | ANSWERED | 12 / 11 | 통과 |
| B. 질병 사실 + 학교 행동 | action → disease | 2 | ANSWERED | 18 / 15 | 통과 |
| C. Legal 샘플(부분 근거) + 학교 행동 | legal → action | 2 | PARTIAL | 14 / 11 | 통과 |
| D. 법령 근거 없음 + 학교 행동 | legal → action | 2 | PARTIAL | 11 / 11 | 통과 |
| E. 문서 비교 | crossdoc | 1 | ANSWERED | 11 / 7 | 통과 |

- B의 두 번째 호출(disease_search)에는 `Disease:MEASLES`와 앞선 행동의 ActionConcept ID들이 `context_entity_ids`로 전달됐다.
- **GraphPath:** E의 CrossDoc 경로 11개 모두 실DB 대조를 통과했다(`check_graph_paths`). 발표용 1건(`SchoolAction p0164:a07 -INSTANCE_OF-> ActionConcept REQUEST_MEDICAL_VISIT`, `-SUPPORTED_BY-> Chunk`, `Document -HAS_CHUNK-> Chunk`)은 관계마다 Cypher MATCH로 다시 확인했다.

## 골든 40문항 회귀 (Tool 4개 연결, `golden_eval_main.py --all-tools`)

| 지표 | 결과 |
|---|---|
| 라우팅(주 의도) | 40/40 |
| status 일치(전 문항 채점) | 37/40 |
| 호출 수 분포 | 1회 36, 0회 4(일반·범위 밖), **2회 이상 0** |

- 단순 질문에서 불필요한 추가 호출은 0건이다. 골든셋에는 복합 질문이 없어 루프가 발동하지 않았다. 루프 검증은 위 사례 B~D와 가짜 Tool 테스트로 했다.
- 어제와 달라진 문항은 새로 연결된 Tool로 라우팅된 5문항뿐이다(S-Q03, S-Q04, S-O04, S-D01, S-L01). Action 문항의 인용·Finding 수는 어제와 같다.
- status 불일치 3건:
  - S-Q03, S-L01: legal_search가 고정 샘플만 받아 NO_EVIDENCE를 반환했다. Legal Tool의 현재 범위 한계다.
  - S-T02: 기존부터 남은 함정 문항이다(PARTIAL 기대, ANSWERED).

## 해석과 한계

- **M4 기준 충족**:
  - 1-Tool 종료: A, E
  - 추가 Tool 호출 후 정상 종료: B(충분), C·D(부분)
  - 실DB GraphPath: E
- 재검색은 규칙 기반 신호에만 반응한다. 신호가 없는 복합 질문(예: 표현이 목록에 없는 경우)은 1회로 끝난다. 골든셋에 복합 질문 유형을 추가해야 실제 비율을 잴 수 있다(11/07 골든셋 작업).
- C·D의 법령 부분은 legal_search가 샘플 1개만 받는 한계 때문에 PARTIAL·근거 없음이다. 루프 결함이 아니다.
- B의 답변은 행동 구역 다음에 질병 구역이 나온다. 질병 사실(잠복기 7~21일)이 아래쪽에 있어, 질문 순서에 맞춘 구역 정렬은 UI 작업에서 검토한다.
- CrossDoc 경로 11개 중 2개가 선정 범위 밖(경기도 p.54·56 예전 청크)을 쓴다. CrossDoc 담당자에게 공유할 사항이다.
