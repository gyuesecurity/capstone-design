# capstone-Backend

디지털 증거 관리 플랫폼의 백엔드(API 서버) 저장소입니다.
사용자가 증거 파일을 등록하면 SHA-256으로 무결성을 확인하고, 등록부터 최종 증거 패키지 생성까지의 관리 이력(Chain of Custody)을 기록합니다.

> 현재 상태: **0단계(초기 설정)**. 서버 코드는 아직 이 저장소에 없습니다.
> 기존 FastAPI 서버 코드(프론트엔드 프로젝트 안의 `server/main.py`)는 1단계에서 가져올 예정입니다.

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
- 프론트엔드 코드는 `/api/...` 상대 경로로 API를 호출합니다. 개발 중에는 서로 다른 포트에서 실행하므로, 프론트엔드 코드를 바꾸지 않고 연결하는 방법(프록시 등)을 정한 뒤 이 문서에 추가합니다.

## 현재 폴더 구조

```text
capstone-Backend/
├── .env.example   # 환경변수 견본 (실제 값 없음)
├── .gitignore     # Git에 올리지 않을 파일 목록
└── README.md      # 이 문서
```

## 환경변수

`.env.example`을 복사해 `.env` 파일을 만든 뒤 실제 값을 입력합니다. `.env`는 Git에 올라가지 않습니다.

| 변수 | 상태 | 설명 |
| --- | --- | --- |
| `EVIDENCE_DATA_DIR` | 1단계에서 가져올 기존 코드가 사용 | 증거 파일과 DB 저장 폴더. 비우면 `data/` |
| `EVIDENCE_CORS_ORIGINS` | 1단계 예정 | API 호출을 허용할 프론트엔드 주소 |
| `EVIDENCE_MASTER_KEY` | 6단계 예정 | 증거 파일 암호화 마스터 키 |

실제 비밀번호, 암호화 키, API 키는 이 저장소의 어떤 파일에도 작성하지 않습니다.

## 실행 방법

서버 코드를 가져오는 1단계 이후에 작성합니다.

## 개발 방식

- 기능별 브랜치를 만들어 작업합니다. (예: `chore/initial-setup`, `feature/chain-of-custody`)
- 작업이 끝나면 Pull Request로 검토한 뒤 `main`에 병합합니다.
- 기존 테스트가 모두 통과해야 병합합니다.

## 개발 순서

| 단계 | 브랜치 | 내용 |
| --- | --- | --- |
| 0 | `chore/initial-setup` | `.gitignore`, `.env.example`, README 작성 |
| 1 | `feature/import-existing-server` | 기존 FastAPI 서버와 테스트 가져오기, 화면 제공 코드 분리, CORS 설정 |
| 2 | `refactor/module-structure` | 서버 코드를 기능별 모듈로 분리 (동작 변경 없음) |
| 3 | `feature/immutable-records` | 기준 해시·검증 결과·이력의 수정·삭제 금지 |
| 4 | `feature/chain-of-custody` | 증거별 관리 이력과 해시 체인 |
| 5 | `feature/rbac` | 역할 기반 권한과 증거별 접근 권한 |
| 6 | `feature/encrypted-storage` | 증거 파일 암호화 저장과 복호화 검증 |
| 7 | `feature/reports` | 보고서 생성 API와 PDF |
| 8 | `feature/evidence-package` | 최종 증거 패키지(ZIP) 생성 |

## 무결성 검증의 의미

등록 해시와 재검증 해시가 일치한다는 것은 서버에 저장된 파일이 등록 시점과 같다는 뜻입니다. 등록 이전에 조작되었는지, 내용이 사실인지까지 증명하지는 않습니다.
