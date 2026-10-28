# mle-02-p2-team2
의료관련감염병 관리지침 지식그래프

## 폴더 구조

```text
├── README.md
├── .env.example
├── .gitignore
├── pyproject.toml
├── data/
│   ├── README.md
│   ├── raw/                 # 로컬 원본 데이터, Git 제외
│   └── specs/               # 데이터 명세서, Git 포함
├── notebooks/              # 탐색 및 데이터 분석
└── src/
    ├── collect/            # 지침 문서 수집
    ├── preprocess/         # 텍스트 추출 및 정제
    ├── index/              # 지식그래프 및 검색 인덱스 구성
    ├── rag/                # 검색 및 근거 기반 답변
    └── eval/               # 검색·답변 품질 평가
```

`data/raw/`는 Git에서 제외되므로 새로 내려받은 환경에서는 직접 생성합니다.

## 시작하기

Python 3.11 이상을 사용합니다. 저장소 루트에서 실행하세요.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
New-Item -ItemType Directory -Force data/raw
```

macOS/Linux에서는 가상환경 활성화에 `source .venv/bin/activate`, 환경 파일 복사에
`cp .env.example .env`, 폴더 생성에 `mkdir -p data/raw`를 사용합니다.

`.env.example`에는 변수 이름과 빈 값만 기록합니다. 실제 키와 접속 정보는 로컬
`.env`에 입력하며 커밋하지 않습니다.
API와 데이터베이스 연동 시 해당 변수를 사용하도록 구현합니다.

## 개발 안내

- `src/`의 다섯 패키지는 역할별 구현을 추가할 기본 뼈대입니다.
- 데이터 형식과 수집 규칙은 `data/specs/`에 기록합니다.
- 노트북에서 검증한 재사용 코드는 `src/`로 옮깁니다.
- 의존성은 `pyproject.toml`에서 관리합니다.

기본 문법과 코드 스타일을 확인하려면 다음 명령을 실행합니다.

```powershell
python -m compileall -q src
python -m ruff check src
```
