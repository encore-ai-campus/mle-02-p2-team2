# mle-02-p2-team2

의료관련감염병 관리지침을 지식그래프로 구축하고, 이를 기반으로 근거 중심 답변을 제공하는 팀 프로젝트입니다.

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
