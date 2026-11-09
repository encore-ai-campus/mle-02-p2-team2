# mle-02-p2-team2 — 교육시설 감염병 근거 기반 대응 Q&A (GraphRAG)

학교 교직원이 감염병 의심·확진 사례를 접했을 때, 여러 공식 문서에 흩어진 **학교 행동·담당자·질병별 관리사항·신고 근거**를 찾아 **문서·판본·PDF 쪽**까지 보여주는 근거 기반 Q&A입니다. 공식 PDF에서 학교 행동을 추출·정규화해 **공통 Neo4j 지식그래프**에 적재하고, **메인 LangGraph**가 질문을 분류해 검색 Tool을 골라 근거를 모읍니다.

- 사용자: 초·중·고·특수학교 교직원(학교장·보건교사·담임교사 등), 기본 시연 지역 경기도
- 하지 않는 것: 진단·치료 판단, 등교·격리의 법적 자동 확정, 개인정보 처리, NEIS 연동
- 팀: 주승우(팀장), 송수림, 유종혁 · 발표 2026-11-09

## 구조

```
공식 PDF(3명 분담)
↓ PDF→md→청크(chunk_id)
↓ LLM 학교 행동 추출 → 사람 판정·사전 매핑(정규화)
↓ 번들 검증 → 공통 Neo4j 적재(ID MERGE) → 임베딩
↓ 검색 Tool 4개(읽기 전용)
↓ 메인 LangGraph: 분류 → Tool 선택 → 근거 평가 → 필요 시 추가 Tool → 답변+PDF 인용
```

- 노드 6종: Document · Chunk · SchoolAction · SchoolRole · Disease · ActionConcept
- 관계 6종(방향 고정): `Document-HAS_CHUNK->Chunk`, `Chunk-ABOUT->Disease`, `SchoolAction-SUPPORTED_BY->Chunk`, `SchoolAction-FOR_DISEASE->Disease`, `SchoolAction-DONE_BY{basis}->SchoolRole`, `SchoolAction-INSTANCE_OF->ActionConcept`
- 기준 문서: [계획서 v2.0](교육시설_감염병_GraphRAG_프로젝트_계획서_v2.0.md), [온톨로지 v3.1](교육시설_감염병_GraphRAG_온톨로지_스키마_설명서_v3.1.md), [공통계약 v2.0](교육시설_감염병_LangGraph_공통계약_가이드_v2.0.md), [개발일정표 v2.0](교육시설_감염병_GraphRAG_최종_개발일정표_v2.0.md), [팀 개발 지시서 v1.2](팀_개발_지시서_v1.2.md)

## 담당과 상세 문서

| 담당 | 영역 | 검색 Tool | 코드 | 상세 |
|---|---|---|---|---|
| 주승우 | 학교 매뉴얼·경기도 가이드북, 메인 LangGraph·UI | ActionSearchTool | `src/*/school/`, `src/rag/ui/` | [발표용 총정리](notebooks/school/평가결과/20261107/발표용_전체과정_검색품질_총정리_20261107.md) |
| 송수림 | 호흡기감염병·시설별 인플루엔자·코로나19 지침, DB 점검·시각화 | DiseaseSearchTool, CrossDocGraphTool | `src/*/respiratory/` | [호흡기 README](notebooks/respiratory/README_respiratory.md) |
| 유종혁 | 예방접종·홍역·수족구·진단신고기준·감염병예방법 | LegalSearchTool | `src/*/prevention_legal/` | [예방·법령 README](notebooks/prevention_legal/README_발표초안_예방법령.md) |

팀 진행·검증 기록: [docs/progress.md](docs/progress.md)

## 실행

```bash
# 화면(루트 .env 필요: Neo4j·OpenAI)
uv run --with streamlit streamlit run src/rag/ui/app.py

# 테스트(DB·LLM 없이)
uv run pytest tests -q
```

- Streamlit Community Cloud: Main file path `src/rag/ui/app.py`, 의존성 `src/rag/ui/requirements.txt`, 자격 증명은 Secrets([예시](src/rag/ui/secrets.toml.example)).
- 담당별 Tool 단독 실행·평가 명령은 위 상세 문서에 있습니다.

## 결과와 한계(요약)

- 공통 DB(11/8 호흡기 점검 스냅샷): 노드 2,268 · 관계 3,089, 허용 밖 관계·근거 없는 행동 등 위반 0. ([db_check_result.json](notebooks/respiratory/db_check_result.json))
- 골든 질문 평가는 담당별로 기준이 달라 합치지 않았습니다. 각 상세 문서의 회귀·독립 결과를 따릅니다.
- 11/9 추가: 질문→LLM Cypher 생성 조회 시연(Text2Cypher, 읽기 전용 검사). [사용법](notebooks/respiratory/Text2Cypher_시연_사용법.md)
- 답변은 근거 인용(Citation)을 함께 보여주지만, 법적 판단·진단을 대신하지 않습니다.

## 폴더 구조

```
mle-02-p2-team2/
├── data/
│   ├── README.md
│   ├── raw/            # 로컬 원본 데이터 (Git 제외, 새 환경에서는 직접 생성)
│   └── specs/          # 데이터 형식·수집 규칙 명세 (Git 추적)
├── notebooks/          # 탐색 및 데이터 분석용 노트북
├── src/
│   ├── collect/        # 지침 문서 수집
│   ├── preprocess/     # 텍스트 추출 및 정제
│   ├── index/          # 지식그래프 및 검색 인덱스 구축
│   ├── rag/            # 검색 및 근거 기반 답변 생성
│   └── eval/           # 검색·답변 품질 평가
├── .env.example        # 환경 변수 이름 템플릿 (값은 비워 둠)
├── .env                # API 키 등 개인 설정 (Git 제외, 직접 생성)
├── .python-version     # Python 3.12
├── pyproject.toml      # 의존성 관리
└── README.md
```

## 개발 환경

- Python 3.12
- 패키지 관리: [uv](https://docs.astral.sh/uv/)

저장소 루트에서 실행합니다.

```bash
# 가상환경 생성 및 의존성 설치 (.venv, Python 3.12)
uv sync

# 원본 데이터 폴더 생성 (Git에 포함되지 않음)
mkdir data/raw          # Windows PowerShell: New-Item -ItemType Directory data/raw
```

### 환경 변수

`.env.example`을 참고해 저장소 루트에 `.env` 파일을 직접 만들고 API 키와 접속 정보를 입력합니다. `.env`는 Git에 커밋하지 않습니다.

## 개발 규칙

- 작업 시작 전에 [공통 에이전트 규칙](AGENTS.md)과 [팀 진행 현황](docs/progress.md)을 확인합니다.
- 코드·문서·평가 작업을 마치면 실제 변경·검증·남은 일을 `docs/progress.md`에 갱신하고 같은 PR에 포함합니다. Claude Code는 `CLAUDE.md`에서 공통 `AGENTS.md`를 가져옵니다.
- 데이터 형식과 수집 규칙은 `data/specs/`에 기록합니다.
- 노트북에서 재사용할 코드는 `src/`로 옮깁니다.
- 의존성은 `uv add <패키지>`로 추가해 `pyproject.toml`과 `uv.lock`에 반영합니다.
- 문법·스타일 검사:

```bash
uv run python -m compileall -q src
uv run ruff check src
```
