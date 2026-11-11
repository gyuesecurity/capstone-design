# capstone-Backend

디지털 증거 관리 플랫폼의 백엔드(API 서버) 저장소입니다.
사용자가 증거 파일을 등록하면 SHA-256으로 무결성을 확인하고, 등록부터 최종 증거 패키지 생성까지의 관리 이력(Chain of Custody)을 기록합니다.

> 현재 상태: **1단계 진행 중**. 기존 FastAPI 서버와 테스트를 가져왔고, 서버는 API(`/api/...`)만 제공합니다.
> 프론트엔드 화면은 별도 저장소에 있으며, 개발 중에는 개발용 프록시(`scripts/dev_proxy.py`)로 연결합니다.

## 담당 범위

- Backend API 개발 및 관리
- 데이터베이스(DB) 설계
- 증거 파일 Storage 연동 (파일과 메타데이터 분리 저장)
- 사용자 인증과 역할 기반 접근 권한(RBAC)
- SHA-256 무결성 검증과 Chain of Custody 관리 이력

## 기술 스택

| 구분 | 사용 기술 | 비고 |
| --- | --- | --- |
| Backend | Python, FastAPI | 기존 서버 코드 유지 |
| Database | SQLite | 초기 개발·테스트용 |
| Storage | 서버 내부의 별도 저장 폴더 | 이후 다른 저장소로 확장할 수 있게 설계 |
| 비밀 값 관리 | 환경변수 (`.env`) | 키를 코드에 직접 작성하지 않음 |
| Frontend | HTML, CSS, JavaScript | 별도 저장소. 이 저장소에서는 수정하지 않음 |

## 프론트엔드와의 관계

- 프론트엔드와 백엔드는 분리해서 개발합니다.
- 프론트엔드는 기존 HTML/CSS/JS를 그대로 사용하고, 백엔드는 API만 제공합니다.
- 프론트엔드 코드는 `/api/...` 상대 경로로 API를 호출합니다.
- 개발 중에는 개발용 프록시가 화면과 API를 한 주소(`http://127.0.0.1:5500`)로 묶어 줍니다. 그래서 프론트엔드 코드를 고치지 않고 연결할 수 있습니다. (아래 [개발용 프록시](#개발용-프록시) 참고)

## 개발용 프록시

`scripts/dev_proxy.py`는 개발과 시연에서만 쓰는 중간 서버입니다. Python 기본 기능만 사용하므로 따로 설치할 패키지가 없습니다.

```text
브라우저 ──► 개발용 프록시 (127.0.0.1:5500) ─┬─ /api/... 요청 ──► 백엔드 FastAPI (127.0.0.1:8000)
                                           └─ 그 외 요청 ────► 프론트엔드 폴더의 화면 파일
```

하는 일

- 프론트엔드 폴더의 HTML, CSS, JS, 이미지 파일을 브라우저에 보내 줍니다.
- 주소가 `/api/`로 시작하는 요청은 백엔드로 그대로 전달하고, 백엔드의 응답(상태 코드, 오류 메시지, 파일)을 그대로 돌려줍니다. 로그인 토큰(`Authorization` 헤더)도 그대로 전달합니다.
- 백엔드가 꺼져 있으면 `502`와 `{"detail": "API 서버(...)에 연결할 수 없습니다. ..."}`를 돌려줍니다.

CORS 설정이 따로 필요하지 않은 이유

- 브라우저는 "화면을 받은 주소"와 "API를 부르는 주소"가 다르면(포트만 달라도) 다른 출처로 보고, 서버가 CORS로 허락해야만 응답을 읽게 해 줍니다.
- 프록시를 쓰면 브라우저는 화면과 API를 모두 `127.0.0.1:5500`에서 받는다고 봅니다. 같은 출처이므로 CORS 검사가 일어나지 않습니다.
- 그래서 개발 중에는 백엔드에 CORS 설정을 추가하지 않습니다. 배포 방식이 정해지면 CORS 필요 여부를 다시 결정합니다.

보안을 위한 제한

- 내 컴퓨터(`127.0.0.1`)에서만 접속할 수 있습니다. 같은 와이파이의 다른 기기에서는 열리지 않습니다.
- `--frontend`로 지정한 폴더 안의 파일만 제공합니다. 폴더 밖을 가리키는 경로나 심볼릭 링크는 404로 막고, 폴더 목록은 보여 주지 않습니다.
- 요청 본문은 64 MiB까지만 받습니다. (증거 파일 업로드 한도 50 MB는 백엔드가 따로 검사합니다.)
- 운영(배포) 서버로 쓰지 않습니다.

## 현재 폴더 구조

```text
capstone-Backend/
├── .env.example        # 환경변수 견본 (실제 값 없음)
├── .gitignore          # Git에 올리지 않을 파일 목록
├── README.md           # 이 문서
├── requirements.txt    # 백엔드 실행에 필요한 패키지 목록
├── scripts/
│   └── dev_proxy.py    # 개발용 프록시 (화면 + /api 연결)
├── server/
│   ├── __init__.py
│   └── main.py         # FastAPI 서버 (API만 제공)
└── tests/
    └── test_api.py     # API 테스트
```

## 환경변수

`.env.example`을 복사해 `.env` 파일을 만든 뒤 실제 값을 입력합니다. `.env`는 Git에 올라가지 않습니다.

| 변수 | 상태 | 설명 |
| --- | --- | --- |
| `EVIDENCE_DATA_DIR` | 사용 중 | 증거 파일과 DB 저장 폴더. 비우면 `data/` |
| `EVIDENCE_CORS_ORIGINS` | 보류 (현재 코드에서 사용하지 않음) | 개발 중에는 개발용 프록시로 대체. 배포 방식이 정해지면 다시 결정 |
| `EVIDENCE_MASTER_KEY` | 6단계 예정 | 증거 파일 암호화 마스터 키 |

실제 비밀번호, 암호화 키, API 키는 이 저장소의 어떤 파일에도 작성하지 않습니다.

> 현재 서버는 `.env` 파일을 자동으로 읽지 않고, 운영체제 환경변수만 읽습니다. 아래 실행 방법처럼 실행하기 전에 터미널에서 직접 설정합니다.

## 실행 방법

준비물: **Python 3.10 이상**, 프론트엔드 저장소를 받은 폴더

### 1. 패키지 설치 (처음 한 번)

```bash
pip install -r requirements.txt
```

### 2. 백엔드 실행 (터미널 1)

이 저장소 폴더에서 실행합니다. 개발용 DB는 기존 `data/`와 섞이지 않도록 새 폴더(`EVIDENCE_DATA_DIR`)를 지정합니다.

Windows PowerShell

```powershell
$env:EVIDENCE_DATA_DIR = "C:\capstone-data\dev"
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

macOS / Linux

```bash
EVIDENCE_DATA_DIR=~/capstone-data/dev uvicorn server.main:app --host 127.0.0.1 --port 8000
```

`http://127.0.0.1:8000/api/health`에서 `{"backend": true, ...}`가 보이면 정상입니다. API 문서는 `http://127.0.0.1:8000/docs`에서 볼 수 있습니다.

### 3. 개발용 프록시 실행 (터미널 2)

이 저장소 폴더에서, `--frontend`에 프론트엔드 폴더(`index.html`이 있는 폴더) 경로를 넣습니다.

```bash
python scripts/dev_proxy.py --frontend "프론트엔드 폴더 경로"
```

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `--frontend` | (필수) | 프론트엔드 폴더 경로 |
| `--port` | `5500` | 화면을 열 포트 |
| `--backend` | `http://127.0.0.1:8000` | 백엔드 주소 |

### 4. 브라우저 접속

```text
http://127.0.0.1:5500/index.html
```

종료할 때는 각 터미널에서 `Ctrl + C`를 누릅니다.

### 실행 순서와 주의 사항

- **백엔드를 먼저, 프록시를 나중에** 실행합니다. 순서가 바뀌어도 화면은 열리지만, 백엔드가 켜질 때까지 API 요청은 `502`가 됩니다.
- 화면은 반드시 **프록시 주소(5500)** 로 엽니다. 백엔드 주소(8000)로 열면 화면 파일이 없어서 404가 납니다.
- VS Code Live Server 등 다른 도구로 화면을 열면 `/api` 요청이 백엔드로 가지 않습니다. Live Server도 기본 포트가 5500이므로, 같이 켜 두면 포트 충돌이 납니다.
- `--frontend`에는 프론트엔드 파일만 있는 폴더를 지정합니다. 다른 프로젝트 파일이 섞인 폴더를 지정하면 그 파일들도 화면 파일처럼 열립니다.
- 개발용 DB를 새로 시작하려면 `EVIDENCE_DATA_DIR`에 새 폴더를 지정합니다. 기존 `data/` 폴더는 지우지 않습니다.

### 테스트 실행

```bash
python -m unittest discover -s tests -v
```

## 개발 방식

- 기능별 브랜치를 만들어 작업합니다. (예: `chore/initial-setup`, `feature/chain-of-custody`)
- 작업이 끝나면 Pull Request로 검토한 뒤 `main`에 병합합니다.
- 기존 테스트가 모두 통과해야 병합합니다.

## 개발 순서

| 단계 | 브랜치 | 내용 |
| --- | --- | --- |
| 0 | `chore/initial-setup` | `.gitignore`, `.env.example`, README 작성 |
| 1 | `feature/import-existing-server` | 기존 FastAPI 서버와 테스트 가져오기, 화면 제공 코드 분리, 개발용 프록시 (CORS는 배포 방식 확정 후 결정) |
| 2 | `refactor/module-structure` | 서버 코드를 기능별 모듈로 분리 (동작 변경 없음) |
| 3 | `feature/immutable-records` | 기준 해시·검증 결과·이력의 수정·삭제 금지 |
| 4 | `feature/chain-of-custody` | 증거별 관리 이력과 해시 체인 |
| 5 | `feature/rbac` | 역할 기반 권한과 증거별 접근 권한 |
| 6 | `feature/encrypted-storage` | 증거 파일 암호화 저장과 복호화 검증 |
| 7 | `feature/reports` | 보고서 생성 API와 PDF |
| 8 | `feature/evidence-package` | 최종 증거 패키지(ZIP) 생성 |

## 무결성 검증의 의미

등록 해시와 재검증 해시가 일치한다는 것은 서버에 저장된 파일이 등록 시점과 같다는 뜻입니다. 등록 이전에 조작되었는지, 내용이 사실인지까지 증명하지는 않습니다.
