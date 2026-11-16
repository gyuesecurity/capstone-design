# 백엔드 API 정리 (화면 개발용)

> 작성: C(백엔드) · 기준: `integration/capstone` + 보안 보완(`feature/backend/security-hardening`) · 2026-11-16
> 서버를 켠 뒤 `/docs`에서 주소 목록을 볼 수 있습니다. 이 문서는 `/docs`에 나오지 않는 **응답 모양, 오류 문구, 화면에서 주의할 점**을 정리합니다.

## 0. 공통 규칙

- 모든 API는 `/api/...` 상대 경로로 부릅니다. 개발 중에는 개발용 프록시(`scripts/dev_proxy.py`)가 화면과 API를 한 주소로 묶어 줍니다.
- 로그인이 필요한 API는 `Authorization: Bearer <token>` 헤더를 보냅니다. 토큰이 없거나 틀리면 401 `"로그인이 필요합니다."`, 8시간이 지나면 401 `"로그인이 만료되었습니다. 다시 로그인해 주세요."`
- 오류 응답은 `{"detail": "사용자에게 보여 줄 한글 문구"}`입니다. 화면은 `detail`을 그대로 보여 주면 됩니다.
  - 예외: 요청 본문 모양이 틀린 경우(필수 칸 없음 등) FastAPI가 422와 함께 `detail`을 **배열**로 줍니다.
- 시각 필드
  - `...At`(예: `createdAt`, `registeredAt`)는 UTC ISO 8601 문자열입니다. (예: `2026-11-16T00:33:14.474+00:00`)
  - 보고서·패키지의 `created`는 서버가 만든 **한국 시간 표시 글자**입니다. (예: `2026/11/16 09:33:14`) PDF에 인쇄된 시각과 글자까지 같습니다.
- ID 형식: 증거 `EV-` + 32자리 16진수, 검증 `VERIFY-` + 32자리, 보고서 `RPT-` + 32자리, 패키지 `PKG-` + 32자리
- 해시는 모두 소문자 16진수 64자리(SHA-256)입니다.
- 모든 응답에 보안 헤더(CSP, `X-Frame-Options: DENY`, `nosniff` 등)가 붙고, `/api/` 응답은 브라우저에 저장되지 않습니다(`Cache-Control: no-store`). 화면 파일은 개발용 프록시가 보내므로 이 헤더의 영향을 받지 않습니다.
- **권한이 없을 때 404와 403의 차이**
  - 404: 그 증거·보고서·패키지를 볼 수 없는 사람입니다. 있다는 사실도 알려 주지 않습니다.
  - 403: 볼 수는 있지만 그 작업 권한(다운로드·검증 등)이 없습니다. 이때 관리 이력에 `ACCESS_DENIED`가 남습니다.

## 1. 계정

| API | 보내는 것 | 받는 것 |
| --- | --- | --- |
| `POST /api/auth/register` (201) | `{email, name, password}` | `{token, user: {id, email, name}}` |
| `POST /api/auth/login` | `{email, password}` | `{token, user: {id, email, name, role}}` |
| `POST /api/auth/logout` | (토큰) | `{ok: true}` |
| `PUT /api/auth/password` | `{current_password, new_password}` | `{ok: true}` |
| `GET /api/me` | (토큰) | `{id, email, name, createdAt, role}` |
| `PUT /api/me` | `{name}` (1~100자) | `{id, email, name, createdAt}` |
| `GET /api/health` | — | `{backend: true, maxFileSize, extensions: [...]}` |

- `role`: `USER`(기본) · `ADMIN` · `SYSTEM`. **아직 ADMIN 전용 기능은 없습니다.** 화면에서 역할로 메뉴를 나누지 마세요.
- **로그인 실패 제한:** 같은 이메일로 10분 안에 5번 틀리면 15분 동안 429 `"로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요."` 막힌 동안은 비밀번호가 맞아도 429입니다. 로그인에 성공하면 횟수가 0으로 돌아갑니다.
- **비밀번호를 바꾸면** 지금 쓰는 기기만 로그인이 유지되고, 다른 기기의 토큰은 모두 끊겨 다음 요청부터 401이 됩니다.
- 주요 오류: 회원가입 409 `"이미 등록된 이메일입니다."`, 로그인 401 `"이메일 또는 비밀번호가 올바르지 않습니다."`, 비밀번호 변경 403 `"현재 비밀번호가 올바르지 않습니다."`, 새 비밀번호 길이(8~128자) 422 `"새 비밀번호는 8자 이상이어야 합니다."`

### 활동 기록 `GET /api/audit`

내 활동 최근 100건(최신순): `[{action, evidenceId, evidenceName, evidenceFilename, createdAt, chainStatus}]`

`action` 값 전체 목록 (마이페이지 표시 이름이 필요한 것은 **굵게**)

| action | 뜻 |
| --- | --- |
| `register`, `login`, `logout` | 회원가입, 로그인, 로그아웃 |
| `password_change`, `profile_update` | 비밀번호 변경, 이름 수정 |
| `upload`, `download` | 증거 등록, 원본 다운로드 |
| `verify:MATCH`, `verify:MISMATCH`, `verify:UNAVAILABLE` | 무결성 검증 결과 |
| **`share_update`**, **`share_revoke`** | 증거 공유 추가·수정, 공유 회수 |
| **`report_create`**, **`report_download`** | 보고서 만들기, 보고서 PDF 다운로드 |
| **`package_create`**, **`package_download`** | 패키지 만들기, 패키지 ZIP 다운로드 |

> 지금 `mypage.js`의 표시 이름 목록에는 굵게 표시한 6개가 없어서 영어 그대로 보입니다. 예: `공유 설정`, `공유 회수`, `보고서 생성`, `보고서 PDF 다운로드`, `증거 패키지 생성`, `증거 패키지 다운로드`

`chainStatus`(활동 기록 위조 검사 결과): `VALID`(정상) · `INVALID`(변조 의심) · `LEGACY`(서명 기능 전 기록) · `KEY_UNAVAILABLE`(키가 없어 확인 못 함)

## 2. 증거

| API | 설명 |
| --- | --- |
| `POST /api/evidence` (201) | 등록. `multipart/form-data`: `file`(필수), `name`, `kind`, `source`, `description`, `memo` |
| `GET /api/evidence?scope=owned` | 목록(최신 등록순). `scope`: `owned`(기본, 내 증거) · `shared`(공유받은 증거) · `all` |
| `GET /api/evidence/{id}` | 상세 |
| `GET /api/evidence/{id}/download` | 원본 다운로드 (복호화한 원본, `Content-Disposition`에 원래 파일 이름) |
| `POST /api/evidence/{id}/verify` | 무결성 검증 → `{result: 검증 1건, saved: true}` |

### 증거 1건 모양

```json
{
  "id": "EV-…", "name": "채팅 캡처", "filename": "채팅 캡처.txt",
  "type": "TXT", "mime": "text/plain", "size": 14,
  "kind": "파일", "source": "직접 수집", "description": "", "memo": "",
  "registeredAt": "…", "selectedAt": "… (registeredAt과 같은 값, 호환용)", "date": "2026-11-16",
  "registeredBy": "등록 당시 이름 (옛 기록은 null → '미기록')",
  "sha256": "…",
  "verificationStatus": "NOT_VERIFIED | MATCH | MISMATCH | UNAVAILABLE",
  "status": "미검증 | 일치 | 불일치 | 검증 불가",
  "lastVerification": "검증 1건 또는 null", "verifications": ["최신순"],
  "storage": "server",
  "encryption": { "algorithm": "AES-256-GCM | LEGACY", "keyVersion": "v1 | null" },
  "access": {
    "isOwner": true, "owner": { "id": "…", "name": "지금 이름" },
    "permissions": ["VIEW", "DOWNLOAD", "VERIFY"],
    "canView": true, "canDownload": true, "canVerify": true
  }
}
```

- `access`로 버튼을 켜고 끄면 됩니다. 소유자는 항상 3개 권한이 모두 있습니다.
- `encryption.algorithm`이 `LEGACY`면 암호화 기능 전에 등록된 평문 증거입니다. 다운로드·검증은 똑같이 됩니다.
- 업로드 때 `client_selected_at`을 보내도 서버는 쓰지 않습니다. 등록 시각은 서버가 정합니다.

### 검증 1건 모양

`{id, evidenceId, startedAt, completedAt, expectedHash, currentHash, status, reason, filename, performedBy, failureCode}`

| 상황 | status | failureCode | reason (그대로 표시) |
| --- | --- | --- | --- |
| 원본이 등록 때와 같음 | `MATCH` | `null` | 등록 당시 SHA-256과 일치합니다. |
| 내용이 바뀜 | `MISMATCH` | `HASH_MISMATCH` | 등록 당시 SHA-256과 일치하지 않습니다. |
| 암호문·인증 태그가 바뀜 | `MISMATCH` | `AUTHENTICATION_FAILED` | 암호화 파일 또는 인증 태그가 변경되어 AES-GCM 인증에 실패했습니다. |
| 파일을 못 읽음 | `UNAVAILABLE` | `FILE_UNAVAILABLE` | 저장된 파일을 읽을 수 없어 검증하지 못했습니다. |

- 서버 키 문제 등은 500이고 검증 기록이 남지 않습니다.

### 주요 오류

| 상황 | 코드 | 문구 |
| --- | --- | --- |
| 허용되지 않은 확장자 | 415 | 허용되지 않은 파일 형식입니다. |
| 빈 파일 (0바이트) | 422 | 빈 파일은 등록할 수 없습니다. |
| 확장자와 실제 형식이 다름 (예: 이름만 `.png`로 바꾼 파일) | 415 | 파일 확장자와 실제 파일 형식이 일치하지 않습니다. |
| 50 MB 초과 | 413 | 파일은 50 MB 이하만 등록할 수 있습니다. |
| 다운로드 권한 없음 | 403 | 다운로드 권한이 없습니다. |
| 검증 권한 없음 | 403 | 검증 권한이 없습니다. |
| 원본 파일 없음 | 404 | 원본 파일을 찾을 수 없습니다. |
| 암호문 변조로 다운로드 불가 | 409 | 암호화 파일의 인증 정보가 일치하지 않아 다운로드할 수 없습니다. |

## 3. 관리 이력 (Chain of Custody)

| API | 받는 것 |
| --- | --- |
| `GET /api/evidence/{id}/custody` | `{evidenceId, events: [이벤트]}` (번호순) |
| `GET /api/evidence/{id}/custody/verify` | `{evidenceId, valid, eventCount, lastSequenceNo, lastEventHash, failures: [{code, sequenceNo, message}]}` |

이벤트 1건: `{sequenceNo, eventType, result, eventTime, actor: {id, name}, details, previousEventHash, eventHash}`

- 증거를 볼 수 있는(VIEW) 사람은 이력도 볼 수 있습니다.
- `actor.name`과 `details.actorName`은 **그 작업을 할 때의 이름**입니다. 나중에 이름을 바꿔도 이력은 그대로입니다.

| eventType | result | details (actorName 말고) |
| --- | --- | --- |
| `EVIDENCE_REGISTERED` | SUCCESS | `sha256, filename, size` |
| `EVIDENCE_DOWNLOADED` | SUCCESS / FAILURE | 실패면 `failureCode` |
| `INTEGRITY_VERIFIED` | SUCCESS (일치·불일치) / FAILURE (검증 불가) | `status, verificationId`, 실패면 `failureCode` |
| `PERMISSION_GRANTED` / `PERMISSION_REVOKED` | SUCCESS | `targetUserId, targetName, permission` |
| `ACCESS_DENIED` | FAILURE | `requiredPermission` (`DOWNLOAD`·`VERIFY`·`OWNER`) |
| `REPORT_INCLUDED` | SUCCESS | `reportId, snapshotSequenceNo, snapshotEventHash, verificationId` |
| `PACKAGE_EXPORTED` | SUCCESS | `reportId, packageId, packagedFileSha256, snapshotSequenceNo, snapshotEventHash` |
| `PACKAGE_EXPORTED` | FAILURE | `reportId, packageId, failureCode` (아래 5장) |
| `PACKAGE_DOWNLOADED` | SUCCESS / FAILURE | `packageId`, 실패면 `failureCode` |

- 화면 표시 예시: 등록 · 원본 다운로드 · 무결성 검증 · 권한 부여 · 권한 회수 · 접근 거부 · 보고서 포함 · 패키지 반출 · 패키지 다운로드
- 변조 의심(불일치)도 **검증 작업은 끝났으므로** SUCCESS입니다. 불일치 여부는 `details.status`를 보세요.

## 4. 공유 (소유자만)

| API | 보내는 것 | 받는 것 |
| --- | --- | --- |
| `GET /api/evidence/{id}/shares` | — | `[{id, email, name, download, verify, grantedAt}]` |
| `PUT /api/evidence/{id}/shares` | `{email, download, verify}` | 공유 1건 (같은 모양) |
| `DELETE /api/evidence/{id}/shares/{userId}` | — | `{id, revoked: true}` |

- 보기(VIEW)는 공유하면 항상 포함됩니다. `download`·`verify`는 체크박스 값입니다.
- 같은 사람에게 다시 PUT하면 권한이 그 값으로 바뀝니다.
- 소유자가 아닌 사람(공유받은 사람)이 부르면 403 `"공유 관리는 증거 소유자만 할 수 있습니다."` + `ACCESS_DENIED`(`OWNER`)
- 오류: 404 `"사용자를 찾을 수 없습니다."`, 422 `"증거 소유자에게는 공유할 수 없습니다."`, 회수할 공유가 없으면 404 `"공유 정보를 찾을 수 없습니다."`

## 5. 보고서

| API | 설명 |
| --- | --- |
| `POST /api/reports` (201) | 만들기. `{title, project, description, selected: [증거 ID], options: [...]}` → 상세 모양 |
| `GET /api/reports` | 내 보고서 목록(최신순) |
| `GET /api/reports/{id}` | 상세 |
| `GET /api/reports/{id}/download` | PDF (`application/pdf`) |

- `options`: `basic`, `metadata`, `hash`, `results`, `custody` 중에서 고릅니다. 저장할 때는 이 순서로 정리됩니다.
- `author`는 보내도 무시하고 로그인한 사람 이름을 씁니다.
- 증거는 1~100개입니다. 볼 수 있는 증거만 넣을 수 있고, 공유받은 증거도 됩니다.
- 보고서는 **만든 사람만** 볼 수 있습니다. 수정·삭제 API는 없습니다.
- PDF를 서버에서 만들어서 몇 초 걸릴 수 있습니다. 진행률 없이 끝나면 한 번에 응답합니다.

상세 모양:

```json
{
  "id": "RPT-…", "title": "…", "project": "…", "description": "…",
  "author": "만든 사람 이름", "authorId": "…",
  "date": "2026/11/16", "created": "2026/11/16 09:33:14", "createdAt": "…",
  "selected": ["EV-…"], "options": ["basic", "hash", "results", "custody"],
  "evidenceSnapshot": [{
    "id": "EV-…", "name": "…", "filename": "…", "type": "파일", "fileType": "TXT",
    "date": "2026/11/16 09:30", "registeredAt": "…", "registeredBy": "…",
    "statusCode": "MATCH", "status": "일치", "size": 14, "mime": "…", "sha256": "…", "source": "…",
    "lastVerification": { "id": "VERIFY-…", "status": "MATCH", "completedAt": "…", "performedBy": "…",
                          "currentHash": "…", "reason": "…", "failureCode": null },
    "custodySnapshot": { "sequenceNo": 3, "eventHash": "…" }
  }],
  "summary": { "MATCH": 1, "MISMATCH": 0, "NOT_VERIFIED": 1, "UNAVAILABLE": 0 },
  "file": { "filename": "제목.pdf", "sha256": "…", "size": 24280 }
}
```

- `evidenceSnapshot`은 **보고서를 만든 순간**의 모습입니다. 그 뒤에 검증해도 보고서 안 내용은 바뀌지 않습니다.
- 목록 한 줄: `{id, title, author, created, createdAt, evidenceCount, summary, listStatus: {code, label}}`
  - `listStatus` 우선순위: 불일치 → 검증 불가 → 미검증 → 모두 일치 (예: `{"code": "MISMATCH", "label": "불일치 1건"}`)

| 상황 | 코드 | 문구 |
| --- | --- | --- |
| 증거를 안 고름 | 422 | 보고서에 포함할 증거를 한 개 이상 선택해 주세요. |
| 101개 이상 | 422 | 보고서에는 증거를 최대 100개까지 넣을 수 있습니다. |
| 제목 없음·너무 김 | 422 | 보고서 제목은 1~150자로 입력해 주세요. |
| 사건명·설명이 너무 김 | 422 | 사건명은 200자, 설명은 4000자 이내로 입력해 주세요. |
| 포함 항목이 틀림 | 422 | 보고서에 포함할 내용을 확인해 주세요. |
| 볼 수 없는 증거 | 404 | 증거를 찾을 수 없습니다. |
| 만드는 도중 이력이 계속 바뀜 | 409 | 보고서를 만드는 동안 증거 이력이 변경되었습니다. 다시 시도해 주세요. |
| 서버에 PDF 도구(Pango)가 없음 | 500 | PDF 생성기를 사용할 수 없어 보고서를 만들지 못했습니다. |
| 저장된 PDF가 바뀌었거나 없음 | 409 | 파일 무결성 문제로 복구가 필요한 보고서입니다. 관리자에게 문의해 주세요. |

## 6. 증거 패키지 (ZIP)

| API | 설명 |
| --- | --- |
| `POST /api/packages` (201) | 만들기. `{reportId}` → 상세 모양 |
| `GET /api/packages?reportId=RPT-…` | 그 보고서로 만든 내 패키지 목록(최신순). `reportId`를 빼면 내 패키지 전체 |
| `GET /api/packages/{id}` | 상세 |
| `GET /api/packages/{id}/download` | ZIP (`application/zip`, 파일 이름 `PKG-….zip`) |

- 패키지는 **보고서를 만든 사람만** 만들고 볼 수 있습니다.
- 보고서에 들어 있는 증거 **모두에 다운로드 권한**이 있어야 만들고 내려받을 수 있습니다.
- 원본을 복호화하고 ZIP을 만들어서 몇 초 걸릴 수 있습니다.

상세(=목록 한 줄) 모양:

```json
{
  "id": "PKG-…", "reportId": "RPT-…",
  "creator": "만든 사람 이름", "creatorId": "…",
  "created": "2026/11/16 09:33:14", "createdAt": "…",
  "file": { "filename": "PKG-….zip", "sha256": "…", "size": 27184 },
  "items": [{
    "evidenceId": "EV-…", "evidenceSha256": "…", "packagedFileSha256": "… (항상 evidenceSha256과 같음)",
    "verificationId": "VERIFY-… 또는 null", "verificationStatus": "MATCH | MISMATCH | UNAVAILABLE | NOT_VERIFIED",
    "pathInPackage": "evidence/01_EV-…_파일명.txt",
    "custodySnapshot": { "sequenceNo": 3, "eventHash": "…" }
  }]
}
```

ZIP 안 구성 (모두 `PKG-…/` 폴더 아래):

| 경로 | 내용 |
| --- | --- |
| `manifest.json` | 패키지·보고서 정보, 파일마다 경로·크기·SHA-256 |
| `verification.json` | 증거마다 검증 기록 전체 |
| `custody.json` | 증거마다 관리 이력 원본. 받은 사람이 `eventHash`를 직접 다시 계산해 볼 수 있음 |
| `report/report.pdf` | 보고서 PDF (보고서 다운로드와 같은 파일) |
| `evidence/01_EV-…_파일명` | 증거 원본 (복호화한 파일, 보고서 순서대로) |

| 상황 | 코드 | 문구 |
| --- | --- | --- |
| 남의 보고서·없는 보고서 | 404 | 보고서를 찾을 수 없습니다. |
| 남의 패키지·없는 패키지 | 404 | 패키지를 찾을 수 없습니다. |
| 다운로드 권한 없는 증거가 있음 (만들 때) | 403 | 다운로드 권한이 없는 증거가 포함되어 있습니다. |
| 다운로드 권한이 회수됨 (내려받을 때) | 403 | 다운로드 권한이 회수된 증거가 포함되어 있습니다. |
| 원본 합계 200 MB 초과 | 422 | 패키지에 넣을 원본 파일 크기의 합계가 200 MB를 넘어 패키지를 만들 수 없습니다. |
| 보고서 PDF가 바뀌었거나 없음 | 409 | 보고서 파일 문제가 해결되지 않아 패키지를 만들 수 없습니다. |
| 원본이 기준 해시와 다르거나 변조됨 | 409 | 원본 해시가 기준 해시와 일치하지 않는 증거가 있어 패키지를 만들 수 없습니다. |
| 원본 파일을 못 읽음 | 409 | 원본 파일을 읽을 수 없는 증거가 있어 패키지를 만들 수 없습니다. |
| 만드는 도중 이력이 계속 바뀜 | 409 | 패키지를 만드는 동안 증거 이력이 변경되었습니다. 다시 시도해 주세요. |
| 저장된 ZIP 파일이 없음 | 404 | 패키지 파일을 찾을 수 없습니다. |
| 저장된 ZIP이 바뀜 | 409 | 파일 무결성 문제로 복구가 필요한 패키지입니다. 관리자에게 문의해 주세요. |
| 서버 키 문제 등 | 500 | 패키지를 만들지 못했습니다. |

- 원본 문제로 못 만들면 관리 이력에 `PACKAGE_EXPORTED` FAILURE가 남습니다. 원인 증거에는 원인 코드(`HASH_MISMATCH` · `AUTHENTICATION_FAILED` · `FILE_UNAVAILABLE`)가 남습니다. 그 앞에서 이미 꺼낸 증거에는 `ABORTED`가 남습니다.
