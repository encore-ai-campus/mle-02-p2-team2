# school 골든 질문·Citation 검증표 (v1.1)

> 담당: 주승우 / ActionSearchTool(+ 메인 그래프 라우팅 검증)
> 기준: 공통 계약 v2.0(ToolResult·QueryOutput·response_type), 온톨로지 v3.1
> 상태: **v1 (11/05 작성)** — 선정쪽수 확대(교육부 90쪽·경기도 118쪽)에 맞춰 v0(14문항)를 폐기하고 새로 작성.
> 근거 청크는 **원문 발췌를 먼저 정하고 그 발췌가 들어 있는 청크를 코드로 찾아 붙였다**(검색 결과를 정답으로 쓰지 않음). 73건 모두 청크 원문에서 글자 그대로 확인.
> 쪽수: `pdf_page`=PDF 뷰어 기준, `printed_page`=인쇄 쪽(교육부 PDF−14, 경기도 PDF−10). 쪽 대조는 사용자 확인 완료(11/05).
> 이전판: `school_v0.md`

## 0. 평가 원칙

- **역할은 예시다.** 두 문서 모두 조직·역할은 예시라고 밝힌다. 기대 `DONE_BY.basis`는 기본 `EXAMPLE`, 답변은 원문은 ○○를 담당 예시로 제시처럼 표현한다. 의무로 단정하면 실패.
- **단계를 섞지 않는다.** 질문 상황의 단계를 시스템이 확정하지 않고, 원문이 행동을 배치한 단계를 그대로 보여 준다.
- **강도 표현을 원문대로.** 권장/권고/원칙/바람직하지 않다를 의무·금지로 바꾸면 실패.
- **신고·보고를 구분한다.** 교육청 보고(NEIS), 학교장의 보건소 신고, 의료기관 신고(학교 행동 아님)는 서로 다른 Finding.
- **문서별 행동을 합치지 않는다.** 교육부 매뉴얼과 경기도 가이드북은 문구가 겹쳐도 Finding·Citation을 문서별로 유지한다(중복 정리는 메인 그래프 답변 단계에서).
- **모의훈련 시나리오는 예시다.** 인물·사례 조건을 일반 규칙처럼 쓰지 않는다.
- **라우팅 질문(S-D·S-L·S-G03·S-G04)** 은 school Tool 단독 답이 아니라 메인 그래프의 분류·Tool 선택을 검증한다. 미등록 Tool은 대체 호출하지 않고 연결 오류를 보고한다.

## 1. 질문표

총 40문항 / 개발용 26 / 평가용 14. 평가용은 설정을 고정한 뒤 한 번만 채점하고 개발용과 따로 보고한다. 정답 청크를 코드에 고정해 통과시키지 않는다.

### 1. 대응 단계별 학교 행동

| ID | 질문 | region | 기대 status | 기대 Tool | 근거(CID) | 통과 조건 | 구분 |
|---|---|---|---|---|---|---|---|
| S-Q01 | 학교에서 유증상 학생이 발견되면 담임교사와 보건교사는 각각 무엇을 확인하나요? | GYEONGGI | ANSWERED | action_search | C01, C02, C03, C04, C05, C06 | 대응 제1단계 행동만. NEIS 등록·능동감시(제2단계)를 지금 할 일로 섞으면 실패. 역할은 원문이 제시한 예시로 표현 | dev |
| S-Q02 | 경기도 초등학교에서 성홍열 확진 학생이 1명 나왔어요. 학교(담임·보건교사)는 무엇을 해야 하나요? | GYEONGGI | ANSWERED | action_search | C07, C08, C09, C10 | 대응 제2단계 학교 행동(범위). 질병 사실(전파·치료)은 Disease Tool 몫이므로 학교 행동만 | dev |
| S-Q03 | 성홍열도 학교장이 보건소에 신고해야 하나요? | GYEONGGI | PARTIAL | action_search | C11, C12 | 학교 문서의 8종 목록에 성홍열이 없다는 사실만 인용. 신고 의무 없음으로 단정하지 않고 현행 법 확인 필요를 warnings에. 기대 Tool은 11/06 legal_search→action_search로 변경(정답 근거 C11·C12가 모두 학교 문서이고, 법령 Tool은 보조로 이어서 호출됨) | eval |
| S-Q04 | 보호자에게 연락해 진료를 요청하는 일을 교육부 매뉴얼과 경기도 가이드북은 각각 어떻게 설명하나요? | NATIONAL | ANSWERED | crossdoc_graph_search | C13, C14, C15 | 문서별 Finding 분리. 같은 행동으로 합치지 않고 두 문서의 인용을 따로 둠 | dev |
| S-Q05 | 대응 제2단계에서 능동감시 대상은 누가 정하고 어디까지 하나요? | NATIONAL | ANSWERED | action_search | C16, C17, C18 | 제2단계는 일부만, 제3단계는 전체로 확대된다는 구분 유지 | dev |
| S-Q06 | 능동감시는 언제 끝나나요? | NATIONAL | ANSWERED | action_search | C19 | 종료 조건을 원문대로 | eval |
| S-Q07 | 유행이 확산되는 대응 제3단계에서 학교장은 무엇을 하나요? | GYEONGGI | ANSWERED | action_search | C20, C21, C22 | 역할은 예시. 검토를 시행해야 한다로 바꾸지 않음 | dev |
| S-Q08 | 교무부장은 유행 시 어떤 일을 맡나요? | GYEONGGI | ANSWERED | action_search | C23, C24 | 제3단계(대책 마련)와 복구단계(보충 지휘)를 구분 | dev |
| S-Q09 | 학년부장은 능동감시에서 무엇을 하나요? | GYEONGGI | ANSWERED | action_search | C25 | 담임, 학년부장, 보건교사로 이어지는 보고 흐름 유지 | eval |
| S-Q10 | 유행이 끝나면 학교는 어떤 후속 조치를 하나요? | GYEONGGI | ANSWERED | action_search | C26, C27, C28 | 복구단계 행동(보고·종료 선언·수업 보충·심리지원) | dev |
| S-Q11 | 학교 내 유행은 어떤 기준으로 끝난 것으로 보나요? | NATIONAL | ANSWERED | action_search | C29 | 방역당국이 역학조사를 한 경우 그 판단에 따른다는 단서 포함 | eval |

### 2. 일시적 관찰실

| ID | 질문 | region | 기대 status | 기대 Tool | 근거(CID) | 통과 조건 | 구분 |
|---|---|---|---|---|---|---|---|
| S-O01 | 일시적 관찰실은 어떤 장소에 어떻게 마련하나요? | NATIONAL | ANSWERED | action_search | C30, C31, C32 | 원칙과 권고 강도를 원문대로 | dev |
| S-O02 | 일시적 관찰실에서 학생은 얼마나 머물고 어떻게 관리하나요? | NATIONAL | ANSWERED | action_search | C33, C34 | 기간·방법·심리지원 포함 | dev |
| S-O03 | 일시적 관찰실로 보건실을 써도 되나요? | NATIONAL | ANSWERED | action_search | C35 | 바람직하지 않다이지 금지가 아님. 부득이한 경우의 조건까지 답해야 함 | eval |
| S-O04 | 일시적 관찰실에서 학생을 돌보는 담당자는 누구로 정하나요? 두 문서가 같은가요? | NATIONAL | ANSWERED | crossdoc_graph_search | C36, C37, C38 | 권고이지 의무가 아님. 고위험군·보건교사 제외, 특수학교는 부담임·지원인력 대체까지 | dev |

### 3. 역할별 조치(유행 시)

| ID | 질문 | region | 기대 status | 기대 Tool | 근거(CID) | 통과 조건 | 구분 |
|---|---|---|---|---|---|---|---|
| S-R01 | 식중독이 의심되는 유행에서 영양(교)사는 무엇을 하나요? | NATIONAL | ANSWERED | action_search | C39, C40 | 행동 노드가 없으면(역할 사전 미등록) 원문 인용 Finding으로만 답해야 함. 거짓 DONE_BY 금지 | dev |
| S-R02 | 행정실은 유행 시 소독과 관련해 무엇을 하나요? | NATIONAL | ANSWERED | action_search | C41, C42 | 소독 대상 예시 표현 유지 | dev |
| S-R03 | 유행의심 상황에서 교장·교감은 무엇을 하나요? | NATIONAL | ANSWERED | action_search | C43, C44, C45 | 신고가 필요한 경우라는 조건 유지. 모든 경우 신고로 단정하면 실패 | eval |

### 4. 역학조사 협조 체크리스트

| ID | 질문 | region | 기대 status | 기대 Tool | 근거(CID) | 통과 조건 | 구분 |
|---|---|---|---|---|---|---|---|
| S-E01 | 홍역 환자가 생기면 역학조사에 협조하기 위해 학교는 접촉자를 어떻게 파악하나요? | GYEONGGI | ANSWERED | action_search | C46, C47 | 역학조사 협조 체크리스트 항목. 질병 사실(잠복기 등)을 지어내지 않음 | dev |
| S-E02 | 홍역 의심 환자가 추가로 생기면 누구에게 연락하나요? | GYEONGGI | ANSWERED | action_search | C48 | 관할 보건소 연락 | eval |
| S-E03 | 수두 환자는 언제까지 격리하나요? | GYEONGGI | ANSWERED | action_search | C49 | 기간을 날짜 수로 바꾸지 않고 원문 조건대로 | dev |
| S-E04 | 결핵 접촉자 조사에서 학교는 어떻게 협조하나요? | GYEONGGI | ANSWERED | action_search | C50, C51 | 접촉자 명단 제공·설명회 협조 | dev |

### 5. 특수학교·업무연속성

| ID | 질문 | region | 기대 status | 기대 Tool | 근거(CID) | 통과 조건 | 구분 |
|---|---|---|---|---|---|---|---|
| S-S01 | 특수학교에서 감염병 의심 학생을 이동시킬 때 유의할 점은 무엇인가요? | GYEONGGI | ANSWERED | action_search | C52 | 2m 거리 원칙과 특수학생 예외를 함께 | dev |
| S-S02 | 교육연속성계획(ECP)에는 어떤 내용이 들어가나요? | GYEONGGI | ANSWERED | action_search | C53, C54 | 6개 구성 항목을 빠뜨리지 않음. 반드시는 원문의 핵심업무 정의 문장일 뿐 법적 의무가 아님 | eval |

### 6. 등교중지·출결·보고

| ID | 질문 | region | 기대 status | 기대 Tool | 근거(CID) | 통과 조건 | 구분 |
|---|---|---|---|---|---|---|---|
| S-A01 | 등교중지 기간의 출석인정을 받으려면 어떤 서류가 필요한가요? | GYEONGGI | ANSWERED | action_search | C55, C56, C57 | 진료확인서·소견서·진단서 중 1개, 부득이하면 처방전 | dev |
| S-A02 | 등교중지된 학생은 언제, 어떻게 다시 등교하나요? | GYEONGGI | ANSWERED | action_search | C58, C59 | 원칙·당일 통보·조기 등교 조건 | eval |
| S-A03 | 학교에서 감염병 환자가 생기면 교육청에는 어떻게 보고하나요? | GYEONGGI | ANSWERED | action_search | C60, C61 | NEIS 경로. 보건소 신고와 섞지 않음 | dev |
| S-A04 | 홍역 학생이 확인되면 학교는 보건소 신고 공문을 어떻게 보내나요? | GYEONGGI | ANSWERED | action_search | C62, C63 | 에듀파인 기안, 학교장 결재, 보건소신고일 입력. 의료기관 신고는 학교 행동으로 만들지 않음 | dev |

### 7. 단계 정의·기준(사실)

| ID | 질문 | region | 기대 status | 기대 Tool | 근거(CID) | 통과 조건 | 구분 |
|---|---|---|---|---|---|---|---|
| S-F01 | 학교 감염병 대응 제1단계는 언제 시작해서 언제 끝나나요? | NATIONAL | ANSWERED | action_search | C64, C65 | 시작·종료를 두 문서 근거로 | dev |
| S-F02 | 학교 내 감염병 유행의심 기준은 무엇인가요? | NATIONAL | ANSWERED | action_search | C66 | 두 기준(동일 학급, 공통 공간 노출)을 원문대로 | eval |
| S-F03 | 대응 제2단계의 주요 활동은 무엇인가요? | GYEONGGI | ANSWERED | action_search | C67, C68 | 단계 정의 표를 근거로 | dev |

### 8. 함정 질문

| ID | 질문 | region | 기대 status | 기대 Tool | 근거(CID) | 통과 조건 | 구분 |
|---|---|---|---|---|---|---|---|
| S-T01 | 유증상 학생은 담임교사가 반드시 직접 보건실까지 데려가야 하나요? | NATIONAL | ANSWERED | action_search | C69, C70 | 원문은 권장, 고위험군이면 다른 교사로 대체. 반드시라고 답하면 실패 | dev |
| S-T02 | 학생 한 명이 열이 나서 보건실에 왔어요. 지금 바로 NEIS에 감염병 환자로 등록해야 하나요? | GYEONGGI | PARTIAL | action_search | C71, C72 | NEIS 보고는 의료기관 확인 후(제2단계) 활동으로 배치. 지금 등록하라고 단정하지 않음 | eval |
| S-T03 | 일시적 관찰실에서 보건교사가 학생을 돌봐야 하나요? | NATIONAL | ANSWERED | action_search | C73 | 원문은 담당 권고 대상에서 보건교사를 제외. 반대로 답하면 실패 | dev |

### 9. 범위 밖·근거 없음

| ID | 질문 | region | 기대 status | 기대 Tool | 근거(CID) | 통과 조건 | 구분 |
|---|---|---|---|---|---|---|---|
| S-G01 | 우리 반 학생이 열이 나고 몸에 발진이 있어요. 성홍열인지 판단해 주세요. | NATIONAL | INSUFFICIENT_EVIDENCE | (없음: OUT_OF_SCOPE) | - | 진단은 서비스 범위 밖. 증상으로 질병을 판정하지 않고 의료기관 진료 안내를 warnings에 | dev |
| S-G02 | ○○○ 학생의 등교중지 정보를 NEIS에 대신 입력해 주세요. | GYEONGGI | INSUFFICIENT_EVIDENCE | (없음: OUT_OF_SCOPE) | - | NEIS 연동·자동 입력·개인정보 처리는 서비스 밖. 학생 이름 저장·재사용 금지 | eval |
| S-G03 | 오늘 점심 메뉴 추천해 줘. | NATIONAL | INSUFFICIENT_EVIDENCE | (없음: OUT_OF_SCOPE) | - | response_type=OUT_OF_SCOPE, Tool 호출 0회 | dev |
| S-G04 | 안녕하세요, 이 서비스로 무엇을 할 수 있나요? | NATIONAL | ANSWERED | (없음: GENERAL) | - | response_type=GENERAL, Tool 호출 0회, 서비스 소개만(감염병 사실을 지어내지 않음) | eval |

### 10. 라우팅(다른 Tool)

| ID | 질문 | region | 기대 status | 기대 Tool | 근거(CID) | 통과 조건 | 구분 |
|---|---|---|---|---|---|---|---|
| S-D01 | 홍역의 잠복기는 며칠인가요? | NATIONAL | ANSWERED | disease_search | - | 질병 사실 질문. Action Tool이 아니라 disease_search로 라우팅되어야 함(학교 문서는 보조) | dev |
| S-L01 | 감염병예방법상 학교장의 신고 의무는 무엇인가요? | NATIONAL | PARTIAL | legal_search | - | 법령 질문. legal_search로 라우팅. 제12조 ① 카드가 PARTIAL로 답함(11/06, 법령 Tool 담당 종혁 확인). 학교장이 관리인·경영자·대표자에 해당하는지, 학교가 복지부령 장소인지는 시행규칙으로 확인 필요 | eval |

## 2. Citation 검증표

`excerpt`는 해당 청크 원문에 글자 그대로 들어 있다(공백·`<br>`·표 구분선 차이는 무시). `kind`는 table_row/paragraph.

| CID | 질문 | document_id | chunk_id | pdf | 인쇄 | kind | excerpt(원문) | 비고 |
|---|---|---|---|---:|---:|---|---|---|
| C01 | S-Q01 | moe_school_manual_v3 | moe_school_manual_v3:p0053:b01 | 53 | 39 | paragraph | 감염병 유증상자를 발견하여 의료기관 진료를 통해 감염병 (의심)환자 발생 여부를 확인하는 단계 |  |
| C02 | S-Q01 | moe_school_manual_v3 | moe_school_manual_v3:p0054:b01 | 54 | 40 | paragraph | 이동 시에는 담임(담당) 교사가 동행하며 2m 정도의 거리 유지 |  |
| C03 | S-Q01 | moe_school_manual_v3 | moe_school_manual_v3:p0055:b01 | 55 | 41 | paragraph | 감염병을 의심할 수 있는 증상인지를 확인하고 의료기관 진료 여부를 물어봄 |  |
| C04 | S-Q01 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0166:t01r01 | 166 | 156 | table_row | 보호자에게 연락하여 의료기관 진료 요청 |  |
| C05 | S-Q01 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0166:t01r01 | 166 | 156 | table_row | 보건교사에게 연락(마스크 착용 필요 여부 확인) |  |
| C06 | S-Q01 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0167:t01r03 | 167 | 157 | table_row | 감염병을 의심할 수 있는 증상인지를 확인하고 의료기관 진료 여부를 물어봄 |  |
| C07 | S-Q02 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0164:t01r09 | 164 | 154 | table_row | 나이스(NEIS)를 통해 교육(지원)청에 보고함 |  |
| C08 | S-Q02 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0166:t01r02 | 166 | 156 | table_row | 결석, 조퇴, 지각한 학생의 사유를 확인하고, 학급 학생들의 증상 유무를 관찰하는 |  |
| C09 | S-Q02 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0168:t01r01 | 168 | 158 | table_row | 나이스(NEIS)로 교육(지원)청에 보고 |  |
| C10 | S-Q02 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0160:t01r03 | 160 | 150 | table_row | 항생제 치료 시작 후 24시간까지 | 같은 발췌가 p0160:t01r01 에도 있음 |
| C11 | S-Q03 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0164:t01r09 | 164 | 154 | table_row | 신고가 필요한 법정감염병인 경우 학교장은 관할 보건소장에게 신고 |  |
| C12 | S-Q03 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0164:b01 | 164 | 154 | paragraph | 종류: 콜레라, 장티푸스, 파라티푸스, 세균성이질, 장출혈성 대장균 감염증, A형간염, 홍역, 결핵 |  |
| C13 | S-Q04 | moe_school_manual_v3 | moe_school_manual_v3:p0057:b02 | 57 | 43 | paragraph | 보호자에게 의료기관 진료 여부를 확인하고 미진료 시 진료를 받도록 하며 |  |
| C14 | S-Q04 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0166:t01r01 | 166 | 156 | table_row | 보호자에게 연락하여 의료기관 진료 요청 |  |
| C15 | S-Q04 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0204:b03 | 204 | 194 | paragraph | 보호자에게 연락하여 의료기관 진료를 요청하고 |  |
| C16 | S-Q05 | moe_school_manual_v3 | moe_school_manual_v3:p0060:t01r03 | 60 | 46 | table_row | 보건(담당)교사가 능동감시 대상을 결정 |  |
| C17 | S-Q05 | moe_school_manual_v3 | moe_school_manual_v3:p0060:t01r03 | 60 | 46 | table_row | 감염병(의심)환자가 발생한 학급이 있는 동일한 층의 학급 |  |
| C18 | S-Q05 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0164:t01r09 | 164 | 154 | table_row | 학교 내 능동감시체계 운영 방안에 따라 감시범위와 방법을 결정하여 실시 |  |
| C19 | S-Q06 | moe_school_manual_v3 | moe_school_manual_v3:p0060:t01r04 | 60 | 46 | table_row | 마지막 (의심)환자 발생일로부터 해당 감염병의 최대 잠복기까지 추가 (의심)환자가 발생하지 않는 경우 |  |
| C20 | S-Q07 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0165:t02r03 | 165 | 155 | table_row | 학교감염병관리조직의 기능을 강화할 수 있도록 조치함 |  |
| C21 | S-Q07 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0165:t02r03 | 165 | 155 | table_row | 유행 확산 정도에 따라 학사일정 조정 및 각종 교육 활동에 대한 제한 검토 및 실시 |  |
| C22 | S-Q07 | moe_school_manual_v3 | moe_school_manual_v3:p0040:t01r04 | 40 | 26 | table_row | 단축수업 및 자체 휴업 실시 필요성 검토 |  |
| C23 | S-Q08 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0165:t02r06 | 165 | 155 | table_row | 출결관리 및 수업결손 대책 마련 |  |
| C24 | S-Q08 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0165:t02r07 | 165 | 155 | table_row | 대응 제3단계에서 수립한 수업결손 대책에 따라 수업 보충 지휘 |  |
| C25 | S-Q09 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0165:t02r08 | 165 | 155 | table_row | 매일 1교시 후 담임교사로부터 전달받은 사항을 보건(담당)교사에게 보고 |  |
| C26 | S-Q10 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0165:t01r04 | 165 | 155 | table_row | 발생 현황 및 조치 결과를 학교장과 교육(지원)청에 보고하고 유행종료 가정통신문(안) |  |
| C27 | S-Q10 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0165:t01r05 | 165 | 155 | table_row | 학교장은 「학교감염병관리조직」의 유행 시 대응 활동을 중단하고 예방단계로 복귀를 명령함 |  |
| C28 | S-Q10 | moe_school_manual_v3 | moe_school_manual_v3:p0068:t02r03 | 68 | 54 | table_row | 심리지원이 필요한 경우 상담교사 및 Wee 센터와 연계, 실시 |  |
| C29 | S-Q11 | moe_school_manual_v3 | moe_school_manual_v3:p0068:t02r01 | 68 | 54 | table_row | 학교 내 해당 감염병 (의심)환자가 모두 완치되고, 최대 잠복기까지 추가 (의심)환자 발생이 없는 경우 |  |
| C30 | S-O01 | moe_school_manual_v3 | moe_school_manual_v3:p0056:t01r01 | 56 | 42 | table_row | 1층에 마련이 원칙 |  |
| C31 | S-O01 | moe_school_manual_v3 | moe_school_manual_v3:p0056:t01r01 | 56 | 42 | table_row | 문을 닫을 수 있고 환기가 잘 되는 공간 |  |
| C32 | S-O01 | moe_school_manual_v3 | moe_school_manual_v3:p0056:t01r01 | 56 | 42 | table_row | 출입구에 안내문을 부착하여 다른 사람들의 접근 차단 |  |
| C33 | S-O02 | moe_school_manual_v3 | moe_school_manual_v3:p0056:t01r03 | 56 | 42 | table_row | 의료기관 진료받으러 가기 전까지 한시적으로 격리 |  |
| C34 | S-O02 | moe_school_manual_v3 | moe_school_manual_v3:p0056:t01r04 | 56 | 42 | table_row | 관찰실 내에서 교사와 학생은 2m 이상의 거리 유지 |  |
| C35 | S-O03 | moe_school_manual_v3 | moe_school_manual_v3:p0056:t01r01 | 56 | 42 | table_row | 보건실은 다른 사람과의 접촉 가능성이 있으므로 바람직하지 않으나 부득이한 경우 다른 침대와 2m 이상을 유지하거나 가림막 |  |
| C36 | S-O04 | moe_school_manual_v3 | moe_school_manual_v3:p0056:t01r02 | 56 | 42 | table_row | 학생의 심리적 안정을 위해 담임교사가 담당할 것을 권고하되 학교 상황에 따라 자율적으로 결정 |  |
| C37 | S-O04 | moe_school_manual_v3 | moe_school_manual_v3:p0056:t01r02 | 56 | 42 | table_row | 고위험군 및 보건교사는 제외 |  |
| C38 | S-O04 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0245:t02r02 | 245 | 235 | table_row | 학생의 심리적 안정을 위해 담임교사가 담당하는 것을 권장함 |  |
| C39 | S-R01 | moe_school_manual_v3 | moe_school_manual_v3:p0043:t01r03 | 43 | 29 | table_row | 보존식 보존 및 조리도구 등 소독 실시 금지 |  |
| C40 | S-R01 | moe_school_manual_v3 | moe_school_manual_v3:p0043:t01r03 | 43 | 29 | table_row | 역학조사 관련 서류 준비 및 협조 |  |
| C41 | S-R02 | moe_school_manual_v3 | moe_school_manual_v3:p0040:t01r03 | 40 | 26 | table_row | 보건실, 교실, 일시적 관찰실 등의 환기 및 소독 |  |
| C42 | S-R02 | moe_school_manual_v3 | moe_school_manual_v3:p0044:t01r01 | 44 | 30 | table_row | 교내 방역물품(손세척제, 손소독제, 마스크, 환경소독제 등) 비치 및 배포 |  |
| C43 | S-R03 | moe_school_manual_v3 | moe_school_manual_v3:p0040:t01r04 | 40 | 26 | table_row | 신고가 필요한 경우 관할 보건소장에게 신고 |  |
| C44 | S-R03 | moe_school_manual_v3 | moe_school_manual_v3:p0040:t01r04 | 40 | 26 | table_row | 학생 감염병 관리조직 활성화시킴 |  |
| C45 | S-R03 | moe_school_manual_v3 | moe_school_manual_v3:p0043:t01r01 | 43 | 29 | table_row | 학교 교직원 대상 긴급회의 소집 |  |
| C46 | S-E01 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0203:t01r09 | 203 | 193 | table_row | 홍역 환자가 발진 전후 4일까지 접촉한 접촉자 명단 확보 |  |
| C47 | S-E01 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0203:t01r10 | 203 | 193 | table_row | 마지막 노출일로부터 21일까지 홍역 증상이 나타나는지 모니터링 |  |
| C48 | S-E02 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0203:t01r17 | 203 | 193 | table_row | 추가 (의심)환자 발생 시 즉시 관할 보건소에 연락 |  |
| C49 | S-E03 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0185:t01r12 | 185 | 175 | table_row | 수두 환자는 모든 피부 병변에 가피가 형성될 때까지 격리 |  |
| C50 | S-E04 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0171:t01r03 | 171 | 161 | table_row | 보건소에 신속히 접촉자 명단 제공 |  |
| C51 | S-E04 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0171:t01r04 | 171 | 161 | table_row | 결핵역학조사 대상자들의 학부모에 대한 설명회 시 적극 협조 |  |
| C52 | S-S01 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0245:t02r01 | 245 | 235 | table_row | 특수학생의 특성상 필요시 라텍스 장갑과 마스크를 착용하고 손을 잡고 |  |
| C53 | S-S02 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0307:t01r01 | 307 | 297 | table_row | 체계적인 대응 및 의사결정을 위해 비상조직체계 구성 |  |
| C54 | S-S02 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0307:t01r02 | 307 | 297 | table_row | 반드시 지속해서 수행해야 하는 핵심업무를 선정 |  |
| C55 | S-A01 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0204:b03 | 204 | 194 | paragraph | ‘진료확인서’, ‘의사 소견서’, ‘진단서’ 중 1개 |  |
| C56 | S-A01 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0204:b02 | 204 | 194 | paragraph | 진료 결과 감염병이 아니었다 하더라도 결과 확인까지의 기간은 출석으로 인정 |  |
| C57 | S-A01 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0205:b01 | 205 | 195 | paragraph | 부득이한 경우 처방전 |  |
| C58 | S-A02 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0205:b01 | 205 | 195 | paragraph | 원칙적으로 의사나 보건소의 의견에 따름 |  |
| C59 | S-A02 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0205:b01 | 205 | 195 | paragraph | 등교가 재개되며 이때 담임교사는 학생이 등교한 당일 해당 사실을 보건(담당)교사에게 통보 |  |
| C60 | S-A03 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0206:b02 | 206 | 196 | paragraph | [나이스]-[보건]-[감염병환자관리]-[감염병환자등록] |  |
| C61 | S-A03 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0209:b01 | 209 | 199 | paragraph | [나이스]-[보건]-[감염병환자관리]-[감염병보고제출] |  |
| C62 | S-A04 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0211:b01 | 211 | 201 | paragraph | 해당(학교 관할)보건소를 수신자로 지정하여 학교장 결재 |  |
| C63 | S-A04 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0212:b01 | 212 | 202 | paragraph | 보건소에 신고한 학생의 ‘보건소신고일’을 선택 |  |
| C64 | S-F01 | moe_school_manual_v3 | moe_school_manual_v3:p0053:t01r01 | 53 | 39 | table_row | 의료기관 진료 결과 감염병 (의심)환자 발생을 확인 |  |
| C65 | S-F01 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0164:t01r04 | 164 | 154 | table_row | 감염병 유증상자를 발견한 후부터 의료기관 확인을 통해 감염병(의심) 환자 발생 혹은 감염병이 |  |
| C66 | S-F02 | moe_school_manual_v3 | moe_school_manual_v3:p0062:b01 | 62 | 48 | paragraph | 최대 잠복기 이내에 동일 학급에서 의심 또는 확진 환자 2명 이상 발생한 경우 |  |
| C67 | S-F03 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0164:t01r09 | 164 | 154 | table_row | 나이스(NEIS)를 통해 교육(지원)청에 보고함 |  |
| C68 | S-F03 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0164:t01r08 | 164 | 154 | table_row | 감염병(의심) 환자의 추가 발생을 파악하여 유행의심 여부를 판단 |  |
| C69 | S-T01 | moe_school_manual_v3 | moe_school_manual_v3:p0054:b01 | 54 | 40 | paragraph | 담당 교사는 담임교사 권장 |  |
| C70 | S-T01 | moe_school_manual_v3 | moe_school_manual_v3:p0053:b01 | 53 | 39 | paragraph | 각 학교 상황에 따라 자율적으로 정할 수 있음 |  |
| C71 | S-T02 | gyeonggi_school_guide_2025 | gyeonggi_school_guide_2025:p0164:t01r09 | 164 | 154 | table_row | 나이스(NEIS)를 통해 교육(지원)청에 보고함 |  |
| C72 | S-T02 | moe_school_manual_v3 | moe_school_manual_v3:p0053:t01r01 | 53 | 39 | table_row | 의료기관 진료 결과 감염병 (의심)환자 발생을 확인 |  |
| C73 | S-T03 | moe_school_manual_v3 | moe_school_manual_v3:p0056:t01r02 | 56 | 42 | table_row | 고위험군 및 보건교사는 제외 |  |

## 3. 채점 방법

| 단계 | 방법 | 기준 |
|---|---|---|
| ① 정답 청크 존재 | 모든 CID의 chunk_id가 Neo4j `Chunk`와 일치 | 전수 |
| ② 검색 품질 | Hit@5·MRR. 정답 청크가 있는 문항만 분모(S-G·S-D·S-L 제외) | 현재 수치를 먼저 기록 |
| ③ 구조 검증 | v2 `validate_tool_result`·`validate_query_output` 통과 | 전 문항 |
| ④ DB 대조 | chunk_id 존재·HAS_CHUNK·pdf_page·excerpt 포함·SUPPORTED_BY·GraphPath 실재 | 전 문항(가짜 ID는 실패해야 함) |
| ⑤ 호출 기록 | `tools_used`·`search_rounds`가 실제 호출과 일치, OUT_OF_SCOPE·GENERAL은 0회 | 전 문항 |
| ⑥ 사람 검수 | 문서명→PDF 쪽→발췌→주장·역할·단계·강도 일치 | 함정 질문·평가용 전수 |

## 4. 변경 이력

- v1.1(11/05): S-G01(진단 판정)·S-G02(NEIS 대리 입력)의 기대 Tool을 `(없음: OUT_OF_SCOPE)`로 정정. 계약 v2.0 §12는 진단 확정·시스템 대행을 Tool 호출 없이 범위 밖으로 종료하도록 한다. 질문·정답 근거는 그대로.

- v1(11/05): 14문항 → 40문항. 선정쪽수 확대 반영, 근거 73건을 발췌 기반으로 재확정. v0의 경기도 54·56쪽 시나리오 근거는 새 범위에 없어 제외.
