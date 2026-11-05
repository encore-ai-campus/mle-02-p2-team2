# 적재 계획 (테스트 DB 먼저, 공동 DB는 승인 후) — 초안

> 범위: 예방·법령 자료를 온톨로지 v3.1 노드·관계 형태로 넣는다. **이번에는 테스트 라벨(`PLTest`)과 `test_` 접두사로만 넣고, 확인 후 삭제한다.**
> 공동 DB 적재는 최종 결과 후 팀 합의 뒤에 한다 (회의 결정 2번).

## 1. 넣을 노드

| 노드 | 개수 | 출처 | 비고 |
|---|---|---|---|
| Document | 3 | kdca_measles_2026, kdca_diagnosis_reporting_2026, moleg_infectious_disease_act_21521 | 공동 DB에 아직 없음 |
| Chunk | 26 | 블록 파일 전체 (문단 `b`, 표 행 `t{표}r{행}`) | 표 행 형식은 v3.1 적용 |
| Disease (테스트 사본) | 2 | MEASLES, SCARLET_FEVER | **공동 DB의 노드에 관계를 붙이지 않는다.** `PLTest` 라벨의 사본 노드만 만든다 |
| SchoolRole (테스트 사본) | 1 | 학교[담당교사] → **팀 승인 전까지 테스트 전용 ID** (`test_SCHOOL_TEACHER`) | 기존 역할(보건교사·담임교사)에 연결하지 않음 |
| ActionConcept (테스트 사본) | 2 | 진료 안내 → `test_REQUEST_MEDICAL_VISIT`, 보건소 신고 → `test_REPORT_HEALTH_CENTER` | 공동 DB 개념에 연결하지 않음. 실제 ID는 팀 승인 |
| SchoolAction | 3 | 추출 결과 승인된 3행 | stage UNSPECIFIED, scope NAMED_DISEASES, modality 원문 표현 |

## 2. 넣을 관계 (v3.1 6종)

| 관계 | 연결 | 수 | 조건 |
|---|---|---|---|
| HAS_CHUNK | Document → Chunk | 26 | 블록의 document_id |
| SUPPORTED_BY | SchoolAction → Chunk | 3 | 93쪽 b01 (1·2행), b03 (3행) |
| FOR_DISEASE | SchoolAction → Disease | 3 | MEASLES (원문에 홍역으로 명시) |
| DONE_BY | SchoolAction → SchoolRole | 3 | basis 필수. 1~3행은 EXPLICIT (사용자 승인, 확인 필요 항목) |
| INSTANCE_OF | SchoolAction → ActionConcept | 3 | 사전 ID 중 선택 |
| ABOUT | Chunk → Disease (테스트 사본) | 선택 | 청크 내용이 질병을 설명하는 경우만. 표 전체 청크에는 붙이지 않음 |

## 3. 방법 (테스트 격리 규칙)
- **공동 DB의 기존 노드(Disease, SchoolRole, ActionConcept, Document, Chunk)에는 MATCH·SET·관계 생성을 하지 않는다.**
- 테스트에 쓰는 모든 노드는 `PLTest` 라벨과 `test_` 접두사 ID를 쓴다. 운영 ID 형식과 섞이지 않게 한다.
- 삭제는 `PLTest` 라벨 + `test_` 접두사 노드만 대상으로 하며, 관계가 공유 노드에 남지 않았는지도 확인한다.
1. 노드는 **ID만 MERGE 키**로 쓰고, 나머지 승인된 속성만 SET (수업 35일차 방식, `UNWIND $rows AS row MERGE`).
2. 관계는 끝점을 ID로 MATCH한 뒤 MERGE. 끝점이 없으면 **조용히 건너뛰지 않고 오류**로 기록.
3. 같은 입력을 두 번 넣어 노드·관계 개수가 늘지 않는지 확인.
4. 키 대조: 넣은 ID 목록과 DB에서 다시 읽은 ID 목록이 같은지 확인 (개수만 보지 않음).
5. 확인 후 `PLTest`와 `test_` 접두사가 붙은 것만 삭제, 0개 확인.

## 4. 승인이 필요한 것 (팀 사전 항목)
- 실제 운영 ID(역할·행동 개념)는 팀 승인 후에 정한다. 테스트는 `test_` 사본 ID로만 진행한다.
- 지침상 보건소 신고를 표현하는 개념은 기존 `NOTIFY_HEALTH_CENTER_LEGAL`(학교장 법정 신고)과 다를 수 있으므로, 새 개념 ID를 팀이 승인해야 한다.
- **SchoolRole**: "학교[담당교사]"를 어떤 역할 ID로 둘지 (기존 HEALTH_TEACHER, HOMEROOM_TEACHER에는 맞지 않음 → 새 ID 승인 필요)
- **ActionConcept**: "진료 안내"는 기존 REQUEST_MEDICAL_VISIT가 맞음. "보건소 신고"는 기존 개념과 의미가 달라 새 ID 승인 필요
- **DONE_BY.basis = EXPLICIT**: 지침 역할표는 대부분 예시라는 단서가 있어, 이 역할 배정이 명시인지 Codex 검토가 필요함
- 공동 DB 노드·관계 추가는 이 계획에서 하지 않는다. 테스트 라벨로만 넣는다.
- DONE_BY.basis = EXPLICIT: Codex 검토 결과 승인 (제목이 수행 주체를 명시하고 예시 단서가 없음).

## 5. 지금 하지 않는 것
- 공동 DB에 쓰기
- 새 사전 ID 만들기 (사전은 팀 승인 대상)
- 법 블록에 학교 행동 관계 붙이기 (법 근거는 주장 근거로만 쓴다)

## 6. 테스트 적재 결과 (2026-11-05)
- 승인: 테스트 방식(공동 DB 노드에 관계 금지, `test_` 사본 노드만 사용), 역할 근거 `EXPLICIT`
- 1차 적재: 노드 37, 관계 38
- 2차 재적재: 노드 37, 관계 38 → 중복 없음 ✅
- 청크 ID 키 대조(DB ↔ 블록 파일): 일치 ✅
- 테스트 노드와 공동 DB 노드 사이 관계: 0 ✅
- 삭제 후 테스트 노드 0, 공동 DB 노드 867 (적재 전과 동일) ✅
- 첫 재실행 확인은 확인 코드의 연결 실수로 관계가 늘어났음(적재 코드 문제 아님). 바르게 고쳐 재확인함.
- 추천안 적용: 역할 근거 EXPLICIT / 운영 역할 ID·보건소 신고 개념 ID는 팀 회의 승인 대상 (테스트는 `test_` 사본 사용)
- 다음: 팀 회의에서 운영 ID 승인 → 최종 결과 정리 → 공동 DB 적재는 팀 합의 뒤
