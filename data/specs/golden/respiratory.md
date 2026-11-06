# respiratory 골든 질문·Citation 검증표 (v0)

> 담당:송수림 / Graph:RespiratoryGraph(domain="respiratory")
> 기준:계약 가이드 v1.2 10장(대표 질문·QueryInput·QueryOutput·Citation 검증표),온톨로지 v3.1
> 상태:질문셋 v0(11/2 작성)의 정답을 md 원문과 대조 완료(11/3). QueryInput·QueryOutput 샘플은 11/4 단독 질의 후 추가.
> 쪽수:pdf_page=PDF 뷰어 기준,printed_page=책 인쇄 쪽(코로나19는 인쇄 쪽 없음→null)
> excerpt:md 변환본 글자 그대로(줄바꿈은 공백 하나로 정리해 비교). 최종 시연 전 PDF 원본과 한 번 더 대조.

## 1. 대표 질문(단독 시연·T2용)

| ID | 질문 | 기대 status | 근거 문서 | 비고 |
|---|---|---|---|---|
| R-Q1 | 인플루엔자로 진단받은 학생은 언제까지 등교하면 안 되나? | ANSWERED | kdca_flu_facility_2026 p15 | T2 후보(사실 질문,action_ids=[] 허용) |
| R-Q2 | 인플루엔자 발생 시 학교(교직원)가 참고할 시설별 예방 조치는? | ANSWERED | kdca_flu_facility_2026 p17·p18 | 팀 지정 첫 독립 질문 |
| R-Q3 | 성홍열 진단 학생의 등교중지 기준은? | ANSWERED | kdca_respiratory_2026 p118(·p116) | 11/5 성홍열 2-Graph(T4) 연결 |
| R-Q4 | 성홍열 집단발생 기준과 그때 학교가 할 일은? | PARTIAL 가능 | kdca_respiratory_2026 p116 | 집단발생 관리 주관은 보건소→학교 행동만 Finding |

## 2. 사실 질문(Chunk 검색만으로 답)

| ID | 질문 | 기대 status | 근거 문서 |
|---|---|---|---|
| R-F1 | 인플루엔자 잠복기는? | ANSWERED | kdca_flu_facility_2026 p6 |
| R-F2 | 성홍열 잠복기는? | ANSWERED | kdca_respiratory_2026 p109 |
| R-F3 | 코로나19 잠복기는? | ANSWERED | kdca_covid19_2026 p39 |
| R-F4 | 코로나19 확진 학생의 격리 권고 기간은? | ANSWERED | kdca_covid19_2026 p28 |

## 3. 함정 질문(근거 범위 확인용)

| ID | 질문 | 기대 status | 확인할 점 |
|---|---|---|---|
| R-T1 | 인플루엔자가 유행하면 학교는 휴교해야 하나? | PARTIAL | 원문은 "소독 목적의 휴교·폐쇄는 필요 없음"(p13)만 있음. 유행 시 휴교 여부 일반 규정은 이 문서에 없음→warnings에 "확인 필요". 보조 정답 gyeonggi_school_guide_2025 p236(학생 빈발 감염병은 전파 차단 휴업 권장 안 함) |

보조 정답 p236에는 방역당국의 역학조사 결과 휴업을 권고하는 경우 실시한다는 단서가 있다. ([R-T1 결과의 4위 인용](../../../tests/fixtures/respiratory/R-T1_tool_result.json))

## 4. Citation 검증표(원문 대조 완료)

| ID | document_id | chunk_id | pdf_page | printed_page | excerpt(원문 그대로) | 주의 |
|---|---|---|---|---|---|---|
| R-Q1 | kdca_flu_facility_2026 | kdca_flu_facility_2026:p0015:b01 | 15 | 11 | 해열제 없이 정상체온 회복 후 24시간이 경과할 때까지입니다. | 같은 쪽 단서:해열제 투약 시 마지막 투약부터 48시간,중증·면역저하자는 의사 판단 |
| R-Q2a | kdca_flu_facility_2026 | kdca_flu_facility_2026:p0017:b01 | 17 | 13 | 담임교사 및 학부모에게 | 교직원 ④ 유증상 학생 통보. 담당이 "교직원"(일반)→DONE_BY 없음 |
| R-Q2b | kdca_flu_facility_2026 | kdca_flu_facility_2026:p0018:b01 | 18 | 14 | ⑤ 필요시 학생(유아) 및 학부모 대상 안내문 발송 | "필요시"→modality DISCRETIONARY |
| R-Q2c | kdca_flu_facility_2026 | kdca_flu_facility_2026:p0018:b01 | 18 | 14 | 실내에서는 출입문과 창문을 동시에 열어 환기 하기 | 소독·환기(⑥) |
| R-Q3 | kdca_respiratory_2026 | kdca_respiratory_2026:p0118:b01 | 118 | 98 | 항생제 치료를 시작 후 최소 24시간까지 자택 격리 | 같은 쪽 "등교, 등원, 출근 등 금지". p116에도 등교중지 문구 |
| R-Q4a | kdca_respiratory_2026 | kdca_respiratory_2026:p0116:b01 | 116 | 96 | 7일 이내에 역학적 연관성이 있는 | 동일 학급·반,환자나 의사환자 2명 이상. 주관=시·군·구 보건소 |
| R-Q4b | kdca_respiratory_2026 | kdca_respiratory_2026:p0116:b01 | 116 | 96 | 학부모와 직원들에게 안내문 발송 | 보건소 요청→해당 기관(학교)이 발송 |
| R-F1 | kdca_flu_facility_2026 | kdca_flu_facility_2026:p0006:b01 | 6 | 2 | 1∼4일 (평균 2일) | 물결표가 ∼(U+223C) |
| R-F2 | kdca_respiratory_2026 | kdca_respiratory_2026:p0109:b01 | 109 | 89 | 1~7일(평균 2~5일) | |
| R-F3 | kdca_covid19_2026 | kdca_covid19_2026:p0039:b01 | 39 | null | 1 ~ 14일(평균 5 ~ 7일) | 표 행(개요 표 '잠복기'). 표 행 청크로 바꾸면 chunk_id가 t00r00 형식으로 바뀜 |
| R-F4 | kdca_covid19_2026 | kdca_covid19_2026:p0028:b01 | 28 | null | 호전된 후 24시간 경과 시까지 격리권고 | "권고"(의무 아님),중증·면역저하자는 의사 판단 |
| R-T1 | kdca_flu_facility_2026 | kdca_flu_facility_2026:p0013:b01 | 13 | 9 | 청소나 소독 시에 | "휴교나 폐쇄를 할 필요는 없음"과 같은 문장(줄바꿈으로 나뉨) |

## 5. 추가 시연 질문

| ID | 질문 | 기대 status | 근거 문서 |
|---|---|---|---|
| T01-MEN | 수막구균 감염증 잠복기는? | ANSWERED | kdca_respiratory_2026 p90 |
| T04 | 보호자 연락은 문서마다 누가 하나요? | ANSWERED | gyeonggi_school_guide_2025·kdca_flu_facility_2026·moe_school_manual_v3(문서 3개 모두 인용되면 정답) |

## 6. 판정 기준(11/6 확정)

- 정답이 1순위면 통과, 2~3순위면 부분 통과, 그 밖이거나 없으면 실패. 명시된 보조 정답도 순위 판정에 사용한다.
- R-Q2처럼 정답 쪽이 여럿이면 각 정답 쪽의 순위를 함께 표시한다.
- T04는 세 문서가 모두 인용되면 통과, 하나라도 빠지면 실패로 판정한다.
- 이 판정은 인용 위치·문서 수에 한정하며, 답변의 의미·status 적절성까지 검증한 결과는 아니다.

## 7. 11/6 실행 결과

근거: [수정 후 7문항](../../../notebooks/respiratory/golden_fix_20261106.md), [기존 인용 대조](../../../notebooks/respiratory/golden_check_20261106.md), 아래 연결된 각 결과 fixture. R-Q2·R-Q3 및 T01-MEN·T04는 별도 시점에 저장된 결과이며 최근 7문항과 같은 실행이 아니다.

| ID | 정답 순위 | 판정 |
|---|---|---|
| R-F1 | 1위 | 통과 |
| R-F2 | 5위 | 실패 |
| R-F3 | 1위 | 통과 |
| R-F4 | 5위 | 실패 |
| R-Q1 | 2위 | 부분 통과 |
| R-Q2 | p17 2위, p18 3위 | 부분 통과 |
| R-Q3 | 주 p118 없음, 보조 p116 3위 | 부분 통과 |
| R-Q4 | 2위 | 부분 통과 |
| R-T1 | 주 p13 없음, 보조 p236 4위 | 실패 |
| T01-MEN | p90 1위 | 통과 |
| T04 | 경기도 1위, 인플루엔자 시설지침 5위, 교육부 6위: 세 문서 모두 인용 | 통과 |

R-T1 보조 순위: [R-T1 결과](../../../tests/fixtures/respiratory/R-T1_tool_result.json). T01-MEN: [결과](../../../tests/fixtures/respiratory/T01-MEN_tool_result.json). T04: [결과](../../../tests/fixtures/respiratory/T04_tool_result.json); [T04_live 결과](../../../tests/fixtures/respiratory/T04_live_tool_result.json)도 같은 세 문서를 인용한다.

## 변경 이력
- v0(11/3):질문셋 v0 정답을 md 원문과 대조,chunk_id를 v3.1 ID 규약으로 기록
- v1(11/6): R-T1 보조 정답, 추가 시연 질문, 확정 판정 기준과 저장 결과를 추가.
