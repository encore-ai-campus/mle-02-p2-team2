# school 골든 질문·Citation 검증표 (v0)

> 담당: 주승우 / Graph: SchoolGraph(`domain="school"`)
> 기준: 계약 가이드 v1.2 10장(대표 질문·QueryInput·QueryOutput·Citation 검증표)·11장(T1·T6·T7), 온톨로지 v3.1(stage·scope·`DONE_BY.basis`·7-1 신고·보고 구분), 계획서 v1.1 6장(골든 질문 6~8개 이상, 단일 사실·학교 대응·신고·보고 구분·출결·문서 비교·근거 부족 포함)
> 문서 범위: `moe_school_manual_v3`(교육부 매뉴얼 제3차 개정판), `gyeonggi_school_guide_2025`(경기도 실무 가이드북) — `source_manifest.csv` 참조
> 상태: **v0 (11/4 작성)**. excerpt는 변환본 Markdown(`data/raw/`)과 글자 대조를 마쳤다. 쪽수는 두 시나리오 표(교육부 PDF 36쪽, 경기도 PDF 54쪽)만 원본 PDF와 대조했고, **나머지 쪽은 최종 시연 전 PDF 원본과 한 번 더 대조**한다.
> 쪽수: `pdf_page`=PDF 뷰어 기준(1부터), `printed_page`=인쇄 쪽. 교육부는 인쇄 = PDF − 14, 경기도는 인쇄 = PDF − 10.
> chunk_id: 온톨로지 v3.1 6절 형식. **11/4 청크 생성(`src/preprocess/school/chunks.py`) 후 실제 ID로 확정**했다. 표 행 `tNNrNN`은 표 작업본에서 그 쪽의 표 순서·데이터 행 순서(머리글 제외), 문단 `bNN`은 본문 변환본에서 그 쪽의 청크 순서다.

## 0. 평가 원칙

- **역할은 예시다.** 두 문서 모두 "학교 내 조직구성 및 구성원 역할은 예시"라고 밝힌다(교육부 PDF 53·54·58·60쪽, 경기도 PDF 163·165쪽). 따라서 기대 `DONE_BY.basis`는 기본 `EXAMPLE`이고, 답변은 "원문은 ○○를 담당 예시로 제시"처럼 표현해야 한다. 의무로 단정하면 실패다.
- **단계를 섞지 않는다.** `stage`는 원문이 행동을 배치한 단계다. 유증상 학생 질문(대응 제1단계)에 대응 제2·3단계 행동(NEIS 등록, 능동감시 등)을 "지금 할 일"로 섞으면 실패다. 사용자 상황의 단계를 시스템이 확정하지 않고 단계 정의 근거를 함께 보여 준다.
- **신고·보고를 구분한다.** 교육청 보고(`REPORT_EDU_OFFICE`), 학교장의 보건소 신고(`NOTIFY_HEALTH_CENTER_LEGAL`), 의료기관 신고(학교 행동 아님)는 서로 다른 Finding이다.
- **문서별 SchoolAction을 합치지 않는다.** 두 문서에 같은 행동이 있어도 Finding·Citation을 문서별로 유지하고 `concept_ids`로만 묶는다.
- **모의훈련 시나리오는 예시다.** 교육부 PDF 36쪽·경기도 PDF 54쪽 표는 "진한이" 사례의 모의 시나리오다. 인물명·사례 조건을 일반 규칙처럼 쓰지 않는다.

## 1. 대표 질문 (단독 시연·T1, 11/5 통합 연결)

| ID | 질문 | region | 기대 status | 근거(문서·PDF쪽) | 기대 concept_ids | 통과 조건 |
|---|---|---|---|---|---|---|
| S-Q1 | 학교에서 유증상 학생이 발견되면 담임교사와 보건교사는 각각 무엇을 확인하나요? | GYEONGGI | ANSWERED | 교육부 53~57, 경기도 163·165~167 (보조: 교육부 36, 경기도 54 시나리오) | DETECT_SYMPTOMATIC, CONTACT_HEALTH_TEACHER, ESCORT_TO_HEALTH_ROOM, CONTACT_GUARDIAN, REQUEST_MEDICAL_VISIT, CHECK_MEDICAL_RESULT, EXCLUDE_FROM_SCHOOL | **T1·11/4 게이트 질문.** 담임과 보건교사의 행동을 별도 Finding으로 구분, 모두 대응 제1단계 근거, 역할은 예시로 표현, 제2단계 행동(NEIS 등록·능동감시)을 섞지 않음 |
| S-Q2 | 경기도 초등학교에서 성홍열 확진 학생이 1명 나왔어요. 학교(담임·보건교사)는 무엇을 해야 하나요? | GYEONGGI | ANSWERED(학교 행동 범위) | 경기도 164·166·168(대응 제2단계), 160(성홍열 등교중지 행)·162(주석) | REPORT_EDU_OFFICE, ACTIVE_SURVEILLANCE, HEALTH_EDUCATION, INFORM_GUARDIANS_NOTICE, EXCLUDE_FROM_SCHOOL | **11/5 2-Graph(T4) 학교 쪽 질문.** 공통 절차(`COMMON`)와 성홍열에 명시된 등교중지 기간(`NAMED_DISEASES`, `FOR_DISEASE=SCARLET_FEVER`)을 분리. 단계 정의 근거를 함께 제시하고 단계를 자동 확정하지 않음. 질병 전문 내용은 respiratory 몫 |
| S-Q3 | 성홍열도 학교장이 보건소에 신고해야 하나요? | GYEONGGI | PARTIAL | 경기도 164·210, 교육부 59 | NOTIFY_HEALTH_CENTER_LEGAL, REPORT_EDU_OFFICE | 학교 문서의 "신고가 필요한 법정감염병" 8종 목록에 성홍열이 없다는 사실만 인용. **"신고 의무 없음"으로 단정하지 않고** 현행 법·신고기준 확인 필요를 warnings에 기록(prevention_legal 몫). NEIS 교육청 보고와 별도 Finding |
| S-Q4 | 보호자에게 연락해 진료를 요청하는 일을 교육부 매뉴얼과 경기도 가이드북은 각각 어떻게 설명하나요? | NATIONAL | ANSWERED | 교육부 57(·36 시나리오), 경기도 166·204 | CONTACT_GUARDIAN, REQUEST_MEDICAL_VISIT | **문서 비교 질문.** 문서별 Finding 분리, 같은 concept로 묶되 SchoolAction은 합치지 않음. 경기도 204쪽의 '등교중지 안내문 배부·서류 안내'는 경기도 쪽 Finding에만 |

## 2. 행정·출결·보고 질문

| ID | 질문 | region | 기대 status | 근거(문서·PDF쪽) | 기대 concept_ids | 통과 조건 |
|---|---|---|---|---|---|---|
| S-A1 | 등교중지 기간의 출석인정을 받으려면 어떤 서류가 필요한가요? | GYEONGGI | ANSWERED | 경기도 204·205 | ATTENDANCE_RECOGNITION, ISSUE_EXCLUSION_NOTICE | 진료확인서·의사 소견서·진단서 중 1개, 부득이한 경우 처방전 인정. "감염병이 아니었어도 결과 확인까지 출석 인정" 포함 |
| S-A2 | 등교중지된 학생은 언제, 어떻게 다시 등교하나요? | GYEONGGI | ANSWERED | 경기도 205 | END_EXCLUSION | 원칙적으로 의사·보건소 의견, 등교 당일 담임→보건(담당)교사 통보, 조기 등교·기간 연장 조건 |
| S-A3 | 학교에서 감염병 환자가 생기면 교육청에는 어떻게 보고하나요? | GYEONGGI | ANSWERED | 경기도 206·209·168, 교육부 73 | REPORT_EDU_OFFICE | NEIS 메뉴경로(감염병환자등록→감염병보고제출). 보건소 신고와 섞지 않음 |
| S-A4 | 홍역 학생이 확인되면 학교는 보건소 신고 공문을 어떻게 보내나요? | GYEONGGI | ANSWERED | 경기도 210·211·212, 교육부 59 | NOTIFY_HEALTH_CENTER_LEGAL | 에듀파인 기안 → 보건소 수신·학교장 결재 → 보건소신고일 입력. 신고 대상 목록 원문 인용. 의료기관 신고는 학교 행동으로 만들지 않음(T3 연계) |

## 3. 사실 질문 (Chunk 검색만으로 답, `action_ids=[]` 허용)

| ID | 질문 | region | 기대 status | 근거(문서·PDF쪽) | 통과 조건 |
|---|---|---|---|---|---|
| S-F1 | 학교 감염병 대응 제1단계는 언제 시작해서 언제 끝나나요? | NATIONAL | ANSWERED | 교육부 53(표 3-4), 경기도 164 | 시작: 유증상자 발견 / 종료: 의료기관 진료 결과 (의심)환자 확인 또는 감염병 아님 확인 |
| S-F2 | 학교 내 감염병 '유행의심' 기준은 무엇인가요? | NATIONAL | ANSWERED | 교육부 62 | 최대 잠복기 이내 동일 학급 2명 이상 등 두 기준을 원문대로 |

## 4. 함정·근거 없음 질문

| ID | 질문 | region | 기대 status | 근거 | 확인할 점 |
|---|---|---|---|---|---|
| S-T1 | 유증상 학생은 담임교사가 반드시 직접 보건실까지 데려가야 하나요? | NATIONAL | ANSWERED | 교육부 54, 53(예시 단서) | 원문은 "담임교사 **권장**", 고위험군이면 다른 교사로 대체. modality를 MANDATORY로 만들거나 "반드시"라고 답하면 실패 |
| S-T2 | 학생 한 명이 열이 나서 보건실에 왔어요. 지금 바로 NEIS에 감염병 환자로 등록해야 하나요? | GYEONGGI | PARTIAL | 경기도 164, 교육부 53 | 원문은 NEIS 보고를 **대응 제2단계**(의료기관 확인 후) 활동으로 배치. 단계 정의를 근거로 안내하고 "지금 등록하라"고 단정하지 않음 |
| S-G1 | 우리 반 학생이 열이 나고 몸에 발진이 있어요. 성홍열인지 판단해 주세요. | NATIONAL | INSUFFICIENT_EVIDENCE | — | 진단은 서비스 범위 밖. 증상으로 질병을 판정하지 않고 의료기관 진료 안내 필요를 warnings에 기록. 사실·행동을 지어내지 않음(T7) |
| S-G2 | ○○○ 학생의 등교중지 정보를 NEIS에 대신 입력해 주세요. | GYEONGGI | INSUFFICIENT_EVIDENCE | — | NEIS 연동·자동 제출·개인정보 처리는 서비스 밖(계획서 1장). 학생 이름을 저장·재사용하지 않음 |

## 5. Citation 검증표

표의 `파일`은 excerpt를 대조한 변환본이다: 본문 = `data/raw/` 본문 Markdown, 표 = `*_본문_표.md`. 표 행 청크의 excerpt는 반복 삽입한 머리글을 뺀 **행 원문**이다(계약 v1.2 4-1). 표 변환본의 `<br>`은 줄바꿈으로 보고 비교했다.

| CID | 질문 | document_id | chunk_id(제안) | pdf_page | printed_page | 파일 | excerpt(원문 그대로) | 주의 |
|---|---|---|---|---:|---:|---|---|---|
| C01 | S-Q1, S-F1, S-T2 | moe_school_manual_v3 | moe_school_manual_v3:p0053:b01 | 53 | 39 | 본문 | 감염병 유증상자를 발견하여 의료기관 진료를 통해 감염병 (의심)환자 발생 여부를 확인하는 단계 | 대응 제1단계 개요 |
| C02 | S-Q1, S-T1 | moe_school_manual_v3 | moe_school_manual_v3:p0053:b01 | 53 | 39 | 본문 | 이 매뉴얼에서 제시한 학교 내 조직구성 및 구성원 역할은 예시이므로 각 학교 상황에 따라 자율적으로 정할 수 있음 | `basis=EXAMPLE` 근거. 54·58·60쪽에도 같은 문장 |
| C03 | S-Q1, S-T1 | moe_school_manual_v3 | moe_school_manual_v3:p0054:b01 | 54 | 40 | 본문 | 이동 시에는 담임(담당) 교사가 동행하며 2m 정도의 거리 유지 | |
| C04 | S-Q1, S-T1 | moe_school_manual_v3 | moe_school_manual_v3:p0054:b01 | 54 | 40 | 본문 | 담당 교사는 담임교사 권장 (만약 임신부 등 감염병 고위험군인 경우 다른 교사로 대체) | "권장" → modality는 RECOMMENDED 후보, 의무 아님 |
| C05 | S-Q1 | moe_school_manual_v3 | moe_school_manual_v3:p0055:b01 | 55 | 41 | 본문 | 감염병을 의심할 수 있는 증상인지를 확인하고 의료기관 진료 여부를 물어봄 | 수행자 미기재 문단. 경기도 167쪽 표는 보건교사 행으로 배치(C15) |
| C06 | S-Q1 | moe_school_manual_v3 | moe_school_manual_v3:p0055:b01 | 55 | 41 | 본문 | 발열을 호소하는 경우 체온을 측정하여 실제 발열 여부를 다시 확인 | |
| C07 | S-Q1 | moe_school_manual_v3 | moe_school_manual_v3:p0055:b02 | 55 | 41 | 본문 | 보건(담당)교사가 [표 3-2], [표 3-3], [표 3-5]을 참고하여 일시적 관찰 여부 판단 | 보건교사가 수행자로 명시된 문장 |
| C08 | S-Q1 | moe_school_manual_v3 | moe_school_manual_v3:p0056:b01 | 56 | 42 | 본문 | 등교 중지가 필요한 감염병으로 확진된 경우 격리 기간 동안 등교 중지 | "담임교사의 조치" 아래 항목. 같은 쪽 보건(담당)교사 항목은 제2단계 활동 개시 |
| C09 | S-Q1 | moe_school_manual_v3 | moe_school_manual_v3:p0056:b01 | 56 | 42 | 본문 | 감염병 (의심)환자 발생을 확인한 즉시 추가 (의심)환자 파악 등 대응 제2단계의 활동 수행 | 제1단계→제2단계 전환 조건. 제2단계 행동 자체를 S-Q1 답에 넣지 않음 |
| C10 | S-Q4 | moe_school_manual_v3 | moe_school_manual_v3:p0057:b01 | 57 | 43 | 본문 | 보호자에게 의료기관 진료 여부를 확인하고 미진료 시 진료를 받도록 하며 | 가정에서 발견한 경우(미등교 시) 담임교사 항목 |
| C11 | S-Q1(보조) | moe_school_manual_v3 | moe_school_manual_v3:p0036:t01r02 | 36 | 22 | 표 | 보건교사는 마스크를 착용하고, 체온 측정과 더불어 환자의 증상과 징후를 관찰한 후 감염병이 의심되는지를 확인 | **모의훈련 시나리오.** PDF 대조 완료(11/4). 11/3 임시 청크 `TEMP:school_manual:B0025:02` |
| C12 | S-Q4(보조) | moe_school_manual_v3 | moe_school_manual_v3:p0036:t01r01 | 36 | 22 | 표 | 진한이의 부모님에게 연락하여 진한이를 데리고 근처의 병원에서 진료를 볼 수 있도록 함 | **모의훈련 시나리오.** PDF 대조 완료(11/4). 11/3 임시 청크 `TEMP:school_manual:B0025:01` |
| C13 | S-Q1, S-Q4 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0166:t01r01 | 166 | 156 | 표 | 보호자에게 연락하여 의료기관 진료 요청 | 담임교사·대응 제1단계 행. 같은 행에 "등교중지 안내문 배부" |
| C14 | S-Q1 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0166:t01r01 | 166 | 156 | 표 | 보건교사에게 연락(마스크 착용 필요 여부 확인) | 담임교사·대응 제1단계 행(C13과 같은 청크) |
| C15 | S-Q1 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0167:t01r03 | 167 | 157 | 표 | 감염병을 의심할 수 있는 증상인지를 확인하고 의료기관 진료 여부를 물어봄 | 보건교사·대응 제1단계 행 |
| C16 | S-Q1 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0165:b01 | 165 | 155 | 본문 | 학교 내 조직구성, 팀명칭, 구성원 역할 등은 예시이므로 | `basis=EXAMPLE` 근거. 163쪽에도 같은 취지 문장 |
| C17 | S-Q2, S-Q3, S-T2 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0164:t01r09 | 164 | 154 | 본문 | 나이스(NEIS)를 통해 교육(지원)청에 보고함 | 대응 제2단계 주요 활동. `REPORT_EDU_OFFICE` |
| C18 | S-Q3 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0164:b01 | 164 | 154 | 본문 | 종류: 콜레라, 장티푸스, 파라티푸스, 세균성이질, 장출혈성 대장균 감염증, A형간염, 홍역, 결핵 | 【신고가 필요한 법정감염병】 목록. 성홍열 없음. 원본 작성 시점 기준이며 현행성은 prevention_legal 문서로 확인 |
| C19 | S-Q2 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0164:t01r07 | 164 | 154 | 표 | 학교 내 감염병(의심) 환자 발생을 확인한 순간부터 | 대응 제2단계 기간(단계 정의 근거) |
| C20 | S-Q2 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0168:t01r01 | 168 | 158 | 표 | 나이스(NEIS)로 교육(지원)청에 보고 | 보건교사·대응 제2단계 행 |
| C21 | S-Q2 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0168:t01r01 | 168 | 158 | 표 | 능동감시의 범위와 방법을 학교장에게 보고 | C20과 같은 청크 |
| C22 | S-Q2 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0166:t01r02 | 166 | 156 | 표 | 결석, 조퇴, 지각한 학생의 사유를 확인하고, 학급 학생들의 증상 유무를 관찰하는 | 담임교사·대응 제2단계 행(능동감시) |
| C23 | S-Q2 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0160:t01r03 | 160 | 150 | 표 | 항생제 치료 시작 후 24시간까지 | 성홍열 행의 등교중지(격리) 기간 열. 같은 행 감염 가능 기간 열에도 같은 문구 → 열 이름과 함께 인용. `NAMED_DISEASES`, `ABOUT=SCARLET_FEVER`는 이 행에만 |
| C24 | S-Q2 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0162:b01 | 162 | 152 | 표 | 등교중지가 필요한 감염병으로 확진된 경우 격리 기간 동안 등교중지 실시(이때 격리 기간은 원칙적으로 의사의 소견을 따름) | 표 3-2 주석. 원문 본문 쪽 확인 필요 |
| C25 | S-Q3 | moe_school_manual_v3 | moe_school_manual_v3:p0059:b01 | 59 | 45 | 본문 | 종류: 콜레라, 장티푸스, 파라티푸스, 세균성이질, 장출혈성 대장균 감염증, A형간염, 홍역, 결핵 | 교육부 문서의 같은 목록. 문서별 Finding 유지 |
| C26 | S-A4, S-Q3 | moe_school_manual_v3 | moe_school_manual_v3:p0059:b01 | 59 | 45 | 본문 | 신고방법: 교육(지원)청의 보고절차에 따라 나이스의 전자결재를 통해 교육(지원)청과 보건소에 보고 | 법적 근거로 감염병예방법 제12조·시행규칙 제8조 인용(같은 쪽) |
| C27 | S-Q4 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0204:b02 | 204 | 194 | 본문 | 보호자에게 연락하여 의료기관 진료를 요청하고 | 학교에서 감염병 의심 학생 발견한 경우 |
| C28 | S-A1 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0204:b02 | 204 | 194 | 본문 | 학교 복귀 시 출결 처리를 위하여 ‘진료확인서’, ‘의사 소견서’, ‘진단서’ 중 1개를 제출하도록 안내 | |
| C29 | S-A1 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0204:b01 | 204 | 194 | 본문 | 진료 결과 감염병이 아니었다 하더라도 결과 확인까지의 기간은 출석으로 인정 | |
| C30 | S-A1 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0205:b01 | 205 | 195 | 본문 | 것을 권장하지만 부득이한 경우 처방전 | 처방전은 KOICD 질병분류센터에서 질병코드 확인 후 인정(같은 쪽) |
| C31 | S-A2 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0205:b01 | 205 | 195 | 본문 | 원칙적으로 의사나 보건소의 의견에 따름 | |
| C32 | S-A2 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0205:b01 | 205 | 195 | 본문 | 등교중지 기간이 지나고 감염병 증상이 소실되면 등교가 재개되며 이때 담임교사는 학생이 등교한 당일 해당 사실을 보건(담당)교사에게 통보 | |
| C33 | S-A2 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0205:b01 | 205 | 195 | 본문 | 등교중지 종료 시점 이전에도 감염성이 소실되었다는 의사의 진료확인서 또는 소견서를 제시하면 등교 가능 | |
| C34 | S-A3 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0206:b01 | 206 | 196 | 본문 | 메뉴경로: [나이스]-[보건]-[감염병환자관리]-[감염병환자등록] | |
| C35 | S-A3 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0209:b01 | 209 | 199 | 본문 | 메뉴경로: [나이스]-[보건]-[감염병환자관리]-[감염병보고제출] | 제출 여부가 '미제출'→'제출'로 바뀌었는지 확인(같은 쪽) |
| C36 | S-A3 | moe_school_manual_v3 | moe_school_manual_v3:p0073:b01 | 73 | 59 | 본문 | 감염병환자들을 등록, 일괄등록, 조회, 보고할 수 있는 메뉴이다. | 교육부 문서 쪽 근거 |
| C37 | S-A4 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0210:b01 | 210 | 200 | 본문 | 관할 보건소 신고 대상 감염병명은 붉은색으로 표시됨 | 괄호 안 8종 목록이 이어짐 |
| C38 | S-A4 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0211:b01 | 211 | 201 | 본문 | 해당(학교 관할)보건소를 수신자로 지정하여 학교장 결재 | 결재는 각 학교 전결규정에 따름(같은 쪽) |
| C39 | S-A4 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0212:b01 | 212 | 202 | 본문 | 보건소에 신고한 학생의 ‘보건소신고일’을 선택 | |
| C40 | S-F1 | moe_school_manual_v3 | moe_school_manual_v3:p0053:t01r01 | 53 | 39 | 표 | 의료기관 진료 결과 감염병 (의심)환자 발생을 확인 | [표 3-4] 종료 시점 → 대응 제2단계. r02는 '감염병이 아닌 것으로 확인 → 예방단계' |
| C41 | S-F1 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0164:t01r04 | 164 | 154 | 표 | 감염병 유증상자를 발견한 후부터 의료기관 확인을 통해 감염병(의심) 환자 발생 혹은 감염병이 | 대응 제1단계 기간(줄바꿈으로 이어짐) |
| C42 | S-F2 | moe_school_manual_v3 | moe_school_manual_v3:p0062:b01 | 62 | 48 | 본문 | 최대 잠복기 이내에 동일 학급에서 의심 또는 확진 환자 2명 이상 발생한 경우 | 두 번째 기준(공통 공간 노출)도 같은 쪽 |

## 6. 대표 질문 S-Q1 샘플

### QueryInput

```json
{
  "question": "학교에서 유증상 학생이 발견되면 담임교사와 보건교사는 각각 무엇을 확인하나요?",
  "region": "GYEONGGI",
  "allowed_document_ids": [
    "moe_school_manual_v3",
    "gyeonggi_school_guide_2025",
    "kdca_measles_2026",
    "kdca_diagnosis_reporting_2026",
    "moleg_infectious_disease_act_21521"
  ],
  "task": "대응 제1단계에서 담임교사·보건교사가 확인하거나 수행할 학교 행동과 담당 근거를 찾아줘",
  "preferred_document_ids": ["moe_school_manual_v3", "gyeonggi_school_guide_2025"]
}
```

`allowed_document_ids`는 11/4 기준 `source_manifest.csv`의 전체 문서다. respiratory 문서가 매니페스트에 등록되면 함께 넣는다.

### 기대 Finding (QueryOutput 실행 결과는 SchoolGraph 구현 후 추가)

| # | claim 요지 | 문서 | 역할(basis) | concept_ids | chunk(CID) |
|---|---|---|---|---|---|
| 1 | 유증상 학생 발견 시 담임교사는 보건교사에게 연락한다(마스크 착용 필요 여부 확인) — 원문은 담임교사를 담당 예시로 제시 | 경기도 | HOMEROOM_TEACHER(EXAMPLE) | CONTACT_HEALTH_TEACHER | C14, C16 |
| 2 | 담임교사는 보호자에게 연락하여 의료기관 진료를 요청한다 | 경기도 | HOMEROOM_TEACHER(EXAMPLE) | CONTACT_GUARDIAN 또는 REQUEST_MEDICAL_VISIT(분리 기준 3인 합의 필요) | C13, C16 |
| 3 | 보건교사는 감염병을 의심할 수 있는 증상인지 확인하고 의료기관 진료 여부를 묻는다 | 경기도 | HEALTH_TEACHER(EXAMPLE) | DETECT_SYMPTOMATIC | C15, C16 |
| 4 | 감염병이 의심되는지 확인하고 의료기관 진료 여부를 물으며, 발열 호소 시 체온을 다시 측정한다(수행자 미기재 문단) | 교육부 | DONE_BY 없음 | DETECT_SYMPTOMATIC | C05, C06 |
| 5 | 보건(담당)교사가 일시적 관찰 여부를 판단한다 | 교육부 | HEALTH_TEACHER(EXAMPLE) | 신규 후보 TEMPORARY_OBSERVATION(미승인 → concept_ids=[]) | C07, C02 |
| 6 | 담임(담당) 교사가 동행하여 이동하며, 담당 교사는 담임교사가 권장된다(고위험군이면 대체) | 교육부 | HOMEROOM_TEACHER(EXAMPLE) | ESCORT_TO_HEALTH_ROOM | C03, C04, C02 |
| 7 | 진료 결과 등교중지가 필요한 감염병으로 확진되면 담임교사는 격리 기간 동안 등교중지한다 | 교육부 | HOMEROOM_TEACHER(EXAMPLE) | EXCLUDE_FROM_SCHOOL | C08 |

- 기대 status: `ANSWERED`. warnings 예: "역할은 원문의 예시이며 학교 상황에 따라 조정 가능", "모의훈련 시나리오(C11·C12)는 사례 예시".
- **넣으면 안 되는 Finding:** NEIS 환자 등록(C17·C20, 대응 제2단계), 능동감시(C22), 보건소 신고(C18). 이 행동들은 단계가 다르다.
- C11(교육부 시나리오 보건교사 행)을 근거로 쓴다면 "모의훈련 시나리오의 예시"임을 claim에 남긴다.

## 7. 개발용·평가용 분리

- **평가용(4개):** S-A2, S-F2, S-T2, S-G2 — 설정을 고정한 뒤 한 번만 채점하고 개발용과 따로 보고한다.
- **개발용(10개):** 나머지. 검색·프롬프트·Cypher 조정은 개발용으로만 한다.
- **11/4 게이트(T1) 대표 질문은 개발용 S-Q1**, 11/5 2-Graph 학교 쪽 질문은 S-Q2다.
- 정답 청크를 코드에 고정해 통과시키지 않는다. 정답은 평가에만 쓴다.

## 8. 검증 방법

| 단계 | 방법 | 기준 |
|---|---|---|
| ① 정답 청크 확정 | 청크 생성 후 5장의 `b??`를 실제 블록 번호로 바꾸고, 표 행 번호를 실제 청크와 대조 | 모든 CID의 chunk_id가 Neo4j `Chunk`와 일치 |
| ② 검색 품질 | Hit@5·MRR. 정답 청크가 있는 검색 문항만 분모(G 분류 제외), 문항 수를 함께 기록 | 현재 수치를 먼저 기록하고 개선하며 비교 |
| ③ 구조 검증 | `validate_query_output(result, query)` 통과 | 전 문항 |
| ④ DB 대조 | `chunk_id` 존재·`HAS_CHUNK`·`pdf_page` 일치·excerpt 포함·`SUPPORTED_BY` | 전 문항(T6: 가짜 chunk_id는 실패해야 함) |
| ⑤ 사람 검수 | 문서명→PDF 쪽→발췌→주장·역할·단계·basis·modality 일치 | 대표 질문·함정 질문 전수 |

## 9. 변경 이력

- v0.1(11/4): 청크 생성 후 5장 chunk_id를 실제 ID로 확정(`b??` 제거). C19·C41은 근거가 표 3-3 행('기간' 칸)이라 표 행 청크로 바꾸고 excerpt에서 '기간:' 표지를 뺐다. 42건 모두 해당 청크 원문에 있음을 확인.
- v0(11/4): 14문항 작성(대표 4·행정 4·사실 2·함정 2·근거 없음 2). excerpt 42건을 변환본 Markdown과 글자 대조. 교육부 36쪽·경기도 54쪽은 원본 PDF 대조 완료, 나머지 쪽은 PDF 재대조 필요.
