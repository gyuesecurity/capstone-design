# D 담당 전체 점검 보고서 (N1~N7)

- 점검일: 2026-11-15
- 기준: `feature/d-report-package` @ `fd3c3ff`
- 범위: (A) C 명세 v1 2종(ReportRenderBundle, PackageBuildBundle) 구현 적합성, (B) 역할표 R1~R18·기획서·플로우 차트 요구사항
- 방식: 검토만 함(코드·문서·설정 수정 없음). 실행 확인은 저장소 밖 임시 폴더에서 수행 후 삭제

**한 줄 요약:** C 명세 v1 두 가지 모두 **부적합 0건**입니다(RS 23개, PS 15개 중 부분 적합 2건, 확인 불가 1건). 남은 문제는 역할표 요구(R5·R6·R8·R9·R11·R12)와 명세 사이의 빈틈, 그리고 명세보다 엄격한 D의 검사입니다. 둘 다 C·팀의 결정이 먼저 필요합니다.

## A. 실제 완료 내역과 실행 결과

N5~N7은 커밋 메시지에 번호가 없어서 작업 순서로 짐작해 붙였습니다.

| 단계 | 커밋 | 근거가 되는 파일 |
|---|---|---|
| (이전 P 단계) | `f5609b3` `6a3f77b` `c6aae9d` `0b915a2` `c0f9dcd` `c063eb3` | 구조 정리, WeasyPrint 교체, 폰트, EVIDENCE 접두어 |
| N1 테스트용 묶음 v1 교체 | `0c1f39f`에 포함 | `tests/fixtures/mock_report_bundle*.json` |
| N2 보고서 묶음 형식 검증 | `0c1f39f`에 포함 | `d_package/report_bundle.py`, `errors.py`, `tests/test_d_report_bundle.py` |
| N3 렌더러 v1 적용 | `0c1f39f` | `pdf_renderer.py`, `templates/`, `scripts/preview_report.py`, `tests/test_d_pdf_renderer.py` |
| N4 ZIP 패키징 | `201493d` | `package_builder.py`, `tests/test_d_package_builder.py` |
| N5 최종 점검 (짐작) | 커밋 없음 | — |
| N6 PDF 도구 결정 자료 (짐작) | `fd3c3ff` | `docs/d_pdf_tool_decision.md` |
| N7 마스킹 PoC (짐작) | `f423265`, `e7bcf4f` (`poc/d-masking` 브랜치, push 안 함) | `poc/masking/*` |

- **push 안 된 커밋:** `feature/d-report-package` 기준 0개(로컬·origin·personal 모두 `fd3c3ff`). `poc/d-masking`의 커밋 2개는 로컬에만 있음
- **ruff:** `ruff check d_package tests` → All checks passed
- **unittest:** `python -m unittest discover -s tests -v` → 37개 모두 OK (`test_api` 6개 포함, 건너뛴 테스트 없음)
- **원본 PDF:** `docs/specs/`에 원본 PDF가 없어 사본과 원본의 일치는 **확인 불가**

## B. ReportRenderBundle v1 적합성

"실행 확인"은 저장소 밖 임시 폴더에서 직접 돌려 본 결과입니다.

| 항목 | 근거 | 판정 | 테스트 |
|---|---|---|---|
| RS-1 자동 이스케이프 | `pdf_renderer.py`의 `_env`가 `autoescape=True`. grep 결과 템플릿에 `\|safe` 없음 | 적합 | `test_markup_inputs_printed_literally_without_fetch` |
| RS-2 로컬 파일 접근 차단 | `_is_template_asset`가 `templates/` 안의 실제 `.ttf`·`.css`(`file://`, 호스트 없음)만 허용. `d_package/fonts`는 없음(폰트가 `templates/fonts`로 옮겨짐). 실행 확인: `<img file:///…>`, `<link http://…>`를 넣어도 실제 요청은 폰트 2개뿐 | 적합 | `test_rejects_other_resources`, `test_blocked_resource_aborts_render` |
| RS-3 `render_report_pdf -> bytes`, 파일을 쓰지 않음 | grep 결과 d_package에 `open`, `write_*` 없음 | 적합 | `test_returns_pdf_bytes` |
| RS-4 `__file__` 기준으로 템플릿을 찾음 | `TEMPLATE_DIR = Path(__file__).parent/'templates'`. 실행 확인: 저장소 밖에서 실행해도 `%PDF-` 생성 | 적합 | **없음** |
| RS-5 예외 2종, 입력 오류와 도구 오류 구분 | `__all__`에서 다시 내보냄 | 적합 | `test_invalid_bundle_raises_bundle_error`, `test_weasyprint_import_failure`, `test_conversion_failure_raises_render_error` |
| RS-6 jobId 제거, id, 기준점 객체, 작성자·등록자·검증자 표시 | 템플릿에서 `item.id`, `custodySnapshot.*`, `v(report.author)`, `v(item.registeredBy)`, `v(...performedBy)` 사용. `jobId`는 알 수 없는 필드로 거부 | 적합 | 기준점·null은 있음. **값이 있을 때 작성자·등록자·검증자 인쇄 테스트 없음** |
| RS-7 `"report-v1"` | `validate_report_bundle` | 적합 | `test_rule_failures` |
| RS-8 reportId를 1쪽 본문과 모든 쪽 바닥글에 인쇄 | 템플릿 `@bottom-center`, `.report-id` | 적합 | `test_report_id_in_page1_body_and_footer`, `test_footer_on_every_page` |
| RS-9 created를 받은 글자 그대로, "(KST)" 표기 | `{{ created }} ({{ timeZone }})`. **`registeredAt`, `completedAt` 옆에도 붙임.** 명세 문구는 "일시 옆에"뿐이라 대상이 모호 | 부분 (C 질문) | `test_time_zone_and_korean_text`, `test_time_strings_not_parsed` |
| RS-10 report 필드 형식과 길이 | `_report()` | 적합 | `test_rule_failures`, `test_length_limits_inclusive` |
| RS-11 options와 구역 생략 | 템플릿의 `'x' in opts` 조건 | 적합 | `test_sections_follow_options` |
| RS-12 evidence를 받은 순서대로 | `for item in evidence`. 실행 확인: 순서를 뒤집어도 받은 순서대로 인쇄 | 적합 | **없음** |
| RS-13 필드별 인쇄 조건 | 템플릿 조건이 명세 표와 일치 | 적합 | `test_sections_follow_options` (단일 option만, 조합은 미검증) |
| RS-14 해시 줄바꿈 없음 | `.hash { white-space: nowrap }`, `currentHash`도 동일 | 적합 | `test_hashes_unbroken`. 잘림 여부는 N3에서 일회성 측정(여유 8mm) |
| RS-15 status 글자 그대로 | 네 값만 허용(`STATUSES`), `{{ item.status }}` 그대로. 실행 확인: "불일치" 인쇄됨 | 적합 | 라벨만 테스트, **값 글자 테스트 없음** |
| RS-16 verification 4개 필드와 null 처리 | null이면 "검증 기록: 미기록" | 적합 | `test_nulls_shown_as_unrecorded` |
| RS-17 "관리 이력 기준점: 순번 · 해시" | 템플릿 91행 | 적합 | `test_hashes_unbroken` (정확한 문자열 비교) |
| RS-18 null을 "미기록"으로 | 매크로 `v()` | 적합 | `test_nulls_shown_as_unrecorded` (10건) |
| RS-19 테스트용 묶음 = 명세 예시 | 실행 확인: 명세 JSON 예시와 비교 결과 `True` | 적합 | `test_fixtures_pass` |
| RS-20 결과 캐시 없음 | `lru_cache`, `functools` 없음. Jinja는 템플릿 컴파일만 캐시 | 적합 | `test_same_bundle_twice` |
| RS-21 print·로그 없음, 예외에 입력값 없음 | grep 결과 없음 | 적합 | `test_error_excludes_input_values`, `test_conversion_failure_raises_render_error` |
| RS-22 PDF 도구 결정, requirements | `requirements.txt`에 jinja2·weasyprint, pdfkit 없음. **팀·교수님 결정 기록 없음** (`docs/d_pdf_tool_decision.md`는 결정 자료) | 부분 (결정 **확인 불가**) | — |
| RS-23 한글 폰트 파일과 라이선스 | `templates/fonts/NanumGothic-{Regular,Bold}.ttf`, `OFL.txt`. A의 `origin/d_soyeon:assets/report/` 폰트와 git 해시 동일(Regular `2aa2b20…`, Bold `79a9bdc…`) | 적합 | `test_time_zone_and_korean_text` |

## C. PackageBuildBundle v1 적합성

| 항목 | 근거 | 판정 | 테스트 |
|---|---|---|---|
| PS-1 함수 형태, 다른 파일 읽기·쓰기 없음 | `package_builder.py`에 `open` 없음. output에만 씀 | 적합 | `test_zip_matches_entries` |
| PS-2 예외 2종 | `errors.py` | 적합 | `test_invalid_bundles_write_nothing`, `test_write_failure_raises_build_error` |
| PS-3 표준 라이브러리만 | hashlib, re, zipfile, typing | 적합 | — |
| PS-4 최상위 필드 | `validate_package_bundle`. packageId 최상위 폴더는 경로 접두어 검사로 강제 | 적합 | INVALID 표 |
| PS-5 entries 필드 | `_check_entry` | 적합 (엄격한 부분은 D절) | INVALID 표 |
| PS-6 쓰기 전 검사, 실패 시 output 비어 있음 | `ZipFile`을 열기 전에 검사 | 적합 | `test_invalid_bundles_write_nothing` (`out.getvalue()==b''`) |
| PS-7 순서·이름 그대로, 항목 추가 없음 | entries 순서대로, 정렬 없음 | 적합 | `test_zip_matches_entries`, `test_no_directory_entries_and_metadata` |
| PS-8 report.pdf와 JSON을 받은 그대로 | 렌더러 import 없음. 실행 확인: ZIP 안 report.pdf가 렌더러 출력과 바이트 동일 | 적합 | `test_json_bytes_preserved` |
| PS-9 한글 이름 UTF-8 | zipfile이 0x800 플래그 자동 설정 | 적합 | `test_korean_name_utf8_flag` |
| PS-10 암호화 없음 | `setpassword` 등 없음 | 적합 | 플래그 검사 |
| PS-11 압축 방식과 date_time | DEFLATED, `FIXED_DATE_TIME = 1980-01-01`(현재 시각 아님) | 적합 (보고 사항) | `test_no_directory_entries_and_metadata` |
| PS-12 결과 재사용 없음 | 캐시 없음 | 적합 | `test_same_bundle_twice` |
| PS-13 print·로그 없음 | grep 결과 없음. 메시지는 번호 기반 위치만 | 적합 | 메시지에 PKG 없음 검사 |
| PS-14 C의 결과 검사 기준 그대로 | `assert_matches_bundle`: 열림, 암호화 없음, 목록·크기·SHA 일치 | 적합 | 4개 테스트 |
| PS-15 C의 책임을 대신하지 않음 | 경로를 고치지 않고 거부만 함. size·sha 대조는 명세 요구 | 적합. 경로 추가 검사는 C의 "안전하게 다듬기"와 겹침(D절) | — |

## D. 명세 외 제약과 공통 점검

### D-1. 명세보다 엄격한 검사 (C 답변 전까지 유지)

예시는 모두 실제로 실행해 확인했습니다.

| 위치 | 내용 | 명세대로 보냈는데 거부되는 예 |
|---|---|---|
| `report_bundle._object` | **명세에 없는 필드 거부** (모든 단계) | evidence 항목에 필드 추가 → `FIELD_UNKNOWN` |
| 〃 | **null 칸도 키는 있어야 함** | `project` 키를 빼고 보냄 → `FIELD_MISSING` |
| `REPORT_ID` (두 묶음 공통) | `RPT-` + 소문자 16진수 32자. **두 명세 모두 형식 미규정** | `RPT-123` → `FIELD_FORMAT` |
| `currentHash`, `eventHash` | 소문자 16진수 64자. 명세는 "SHA-256", "문자열 64자"만 | 대문자 해시 → 거부 |
| `report.options` | 중복 거부 | `["basic","basic"]` → 거부 |
| `evidence.size` | 0 이상. 명세는 "정수"만 | `-1` → 거부 |
| 정수 검사 전체 | `True`/`False`를 정수로 인정 안 함 | JSON에서는 생기기 어려움 |
| `registeredAt`, `custodySnapshot` | null 불허 (명세 형식과 일치하나 명시 없음) | null → 거부 |
| `package_builder._check_path` | `.`·빈 세그먼트, `/`로 끝남, `\`, 드라이브 문자, **C1 제어문자(U+0080~009F)** 거부. 명세의 D 검사는 "`..`·절대 경로"까지 | `PKG/./a`, `PKG/a//b`, `a\b`, U+0085 → 거부 |
| 〃 | entries 빈 배열 거부 (명세 규정 없음) | `entries: []` → 거부 |
| 〃 | `data`는 정확히 `bytes`만 | `bytearray` → 거부 |
| 〃 | 명세에 없는 필드 거부 | entry에 필드 추가 → 거부 |

명세보다 **느슨한** 부분: 빈 evidence·빈 options 허용, 증거 ID 형식과 sequenceNo 범위 미검사(`sequenceNo=0` 통과).

### D-2. 공통 점검

| 점검 | 결과 |
|---|---|
| print·로그·파일 쓰기·현재 시각·네트워크·DB 사용 (grep) | d_package에 **없음** |
| PDF 내부 메타데이터 | `/Producer: WeasyPrint 70.0`만 있고 **CreationDate·ModDate·trailer `/ID` 없음** → **위반 없음** |
| 같은 묶음 두 번 렌더링 시 바이트 차이 | 1.2초 간격 두 번 → **바이트 완전 동일** |
| 대용량 (계산상 추정, 측정 안 함) | 모든 `data`가 묶음 안에서 메모리에 올라옴. 50MiB × 10건이면 묶음만 약 500MiB, output이 `BytesIO`면 압축 결과만큼 추가. C에 **output을 임시 파일로** 줄 것과 증거 수·크기 상한 결정 요청 |
| 통합 흐름 (C 방식 호출) | 렌더링 → report 항목 → `build_package_zip(BytesIO)` 결과가 열림·암호화 없음·목록·크기·해시 일치 모두 통과 |
| 증거 100건 성능 | 3.65초, 61쪽, PDF 136KB |

## E. 역할표 대조

| R | 상태 | 근거 | 명세 v1과의 관계 | 부족한 점 | 담당 |
|---|---|---|---|---|---|
| R1 패키지 구조 설계 | 부분 | 명세상 경로는 "C가 정하는 경로의 예". D는 받은 대로 기록 | 책임 어긋남 | 설계 주체가 역할표(D)와 명세(C)에서 다름 | 팀 |
| R2 PDF 보고서 생성 | 충족 | `render_report_pdf`, B절 | 적합 | — | D |
| R3 기본 정보 | 충족 | id, filename, name, size | 적합 | — | D |
| R4 SHA-256 | 충족 | `sha256`, `currentHash` | 적합 | — | D |
| R5 수집·등록 시간 | **부분** | `registeredAt`, `completedAt`, `created`만. **수집 시각·NTP·TSA 필드 없음** | 명세에 필드 없음 | H절 필드안 | C |
| R6 주요 메타데이터 | **부분** | `source`, `mime`뿐, EXIF 없음 | 명세에 필드 없음 | H절 필드안 | C |
| R7 무결성 검증 결과 | 충족 | status, verification | 적합 | 검증 1건만 표시 | D |
| R8 CoC 이력 | **부분** | PDF에는 `custodySnapshot`만, 전체 이력은 ZIP의 `custody.json`(C 생성) | 적합 | "이력 포함" 충족 여부 판단 필요 | 팀 |
| R9 실패 증거 구분 | **미충족** | 상태 글자만 있고 강조·요약 건수·경고 없음. options에 results가 없으면 "불일치"가 인쇄되지 않음. 테스트 없음 | 명세대로면 구분 불가 | options와 무관한 실패 표시 여부 C 질문 | C·팀 → D |
| R10 하나의 패키지 | 충족 (D 몫) | `build_package_zip` | 적합 | 구성은 C | D·C |
| R11 권한 확인 연동 | D 범위 밖, **미충족(C)** | `evidence-sharing:server/db.py`의 `PERMISSION_TYPES`가 VIEW·DOWNLOAD·VERIFY뿐. 보고서·패키지 API 없음 | — | 권한 종류·확인 시점 미정 | C |
| R12 CoC 기록 연동 | D 범위 밖, **부분(C)** | `server/db.py:189`에 `REPORT_INCLUDED`, `PACKAGE_EXPORTED`, `PACKAGE_DOWNLOADED` 정의, **호출 없음** | — | 기록 시점·필드·실패 처리 미정 | C |
| R13 OCR | 부분 (PoC) | `poc/d-masking` Tesseract | 범위 밖 | 한글 일부·저대비·회전 글자 놓침 | D (정책 결정 후) |
| R14 개인정보 탐지 | 미충족 | 구현 없음 | — | I절 정규식안 | D |
| R15 개인정보 마스킹 | 부분 (PoC) | 모든 텍스트를 사각형으로 덮음 | — | 선택 마스킹은 R14 선행 | D |
| R16 얼굴 탐지·마스킹 | 부분 (PoC) | YuNet 7/7 검출 | — | 회전 얼굴 일부 노출, 표본 1인 | D |
| R17 Vision AI | 부분 | YuNet(DNN) 하나 | — | 범위 정의 필요 | 팀 |
| R18 조작 탐지 | 미충족 | 구현 없음 | — | I절 | D·팀 |

## F. 위반·위험

명세 부적합으로 판정한 항목은 없습니다.

| 심각도 | 내용 | 근거 | 권장 조치 |
|---|---|---|---|
| 높음 | 실패 증거를 보고서에서 구분 불가(R9). results를 빼면 상태 자체가 사라짐 | E절 R9 | C·팀 결정 후 D 수정 |
| 높음 | Docker 이미지에 Pango가 없으면 실제 PDF 생성 시에만 실패 | `Dockerfile`, PR #11 코멘트 | C가 Dockerfile 반영 (요청함) |
| 중간 | 권한 확인·CoC 연동 미구현(R11·R12) | E절 | C에 요청 |
| 중간 | 명세 외 제약으로 정상 묶음 거부 가능 | D-1 | C 답변으로 확정 |
| 중간 | PDF 도구가 설계 문서 기본안(fpdf2)과 다르고 결정 미정 | RS-22 | 팀·교수님 결정 |
| 중간 | 큰 증거의 메모리 사용량 | D-2 | C에 알림 |
| 낮음 | "(KST)"를 created 외 두 일시에도 붙임 | RS-9 | C 질문 |
| 낮음 | 테스트 누락: RS-4, RS-12, RS-6 값 표시, RS-15 값 글자 | B절 | D 테스트 추가 (P1) |
| 낮음 | PDF `/Producer`에 도구 버전 노출 | D-2 | C에 확인 |
| 낮음 | CI의 ruff 대상에 d_package 빠짐 | `.github/workflows/ci.yml` | C·CI 담당 |

## G. 플로우 차트 불일치와 수정 요청

1. **"원본 증거 조회"가 D 구간에 있음.** D는 DB·저장소를 조회하지 않음(조회·복호화·해시 확인은 C·B).
   → 이 상자를 C 구간으로 옮기고, D 구간은 "C가 만든 묶음 수신"으로 시작.
2. **"무결성 검증 결과 결합", "수집 시간 및 CoC 결합"이 D 구간에 있음.** 실제 결합은 C, D는 표시만.
   → 결합은 C, 표시·기록은 D로 표기.
3. **순서가 다름.** 그림은 패키지 후 PDF 생성, 명세는 저장된 report.pdf를 ZIP에 그대로 넣음.
   → "PDF 보고서 생성(저장)" → "제출용 패키지 생성(report.pdf 포함)" 순서로.
4. **원본/마스킹 패키지 중 하나만 만드는 분기.** 마스킹본만 제출하면 원본 해시로 검증 불가.
   → 팀 질문: ① 원본을 항상 포함하는가 ② 마스킹본은 별개 파일인가 ③ 파생 관계 CoC 기록 주체(C) ④ entries kind에 `masked` 추가 필요 여부(명세 v2)

## H. C에게 보낼 요청·질문과 결정 필요 항목

**C에게**
1. **R5 필드안:** evidence에 `collectedAt`(문자열|null, 표시용 KST 글자), `timeSource`(`"NTP"`·`"TSA"`·`"DEVICE"`|null), `tsa`({`genTime`, `authority`}|null). C가 표시용 글자로 주고 D는 그대로 인쇄
2. **R6 필드안:** `metadataItems: [{label, value}]`|null. C·A가 고른 항목을 순서대로 인쇄(필드명을 고정하지 않아 명세 재변경 불필요)
3. **R8:** PDF에 기준점만 넣으면 되는지, `custodyEvents[]`(순번·일시·종류·수행자·결과)를 추가해 이력을 인쇄할지
4. **R9:** options에 results가 없어도 불일치·검증 불가·미검증을 표시해도 되는지. 1쪽 요약 건수와 증거별 경고(색이 아닌 글자·테두리) 추가 가능 여부
5. **"(KST)" 표기 대상:** created만인지, registeredAt·completedAt도인지
6. **D-1 명세 외 제약 12건:** 유지/완화 여부 (특히 reportId 형식, 해시 대소문자, null 칸 키 생략, 경로 `.`·`\`·C1 제어문자)
7. **명세 v1.1 형식 추가:** reportId 형식(`RPT-` + 32자리 16진수), 해시 소문자 규칙
8. **R11:** 보고서·패키지 생성 권한(예: `EXPORT`)과 확인 시점
9. **R12:** `REPORT_INCLUDED`·`PACKAGE_EXPORTED` 기록 시점·필드, D 함수 예외 시 기록 내용
10. **대용량:** output을 임시 파일로, 증거 수·크기 상한
11. **메타데이터:** PDF `/Producer` 유지 가능 여부
12. **이미 요청함:** Dockerfile Pango 설치, CI ruff 대상에 d_package 추가

**팀·교수님 결정**
- PDF 도구 (`docs/d_pdf_tool_decision.md`)
- R1 구조 설계 책임 (역할표와 명세 중 무엇을 따를지)
- R8 해석
- 마스킹 정책 (G-4, PoC README 질문 6개)
- 보고서 품질 제안: 페이지 번호 "n / 전체", "해시 검증은 등록 이후의 위변조만 확인 가능" 안내 문구, 100건 3.65초·61쪽 측정값 기준 상한

## I. D 추가 작업

명세 부적합이 없어 **P0 작업은 없습니다.**

| 순위 | 작업 | 선행 조건 | 수정할 파일 |
|---|---|---|---|
| P1 | 빠진 테스트 4종 (RS-4, RS-12, RS-6 값 표시, RS-15 값 글자) | 없음 | `tests/test_d_pdf_renderer.py` |
| P1 | 명세 외 제약 정리 | H-6, H-7 답변 | `report_bundle.py`, `package_builder.py`, 테스트 |
| P2 | R9 실패 증거 구분 표시 | H-4 답변 | 템플릿, 테스트 |
| P2 | R5·R6·R8 필드 표시 | 명세 v2 | `report_bundle.py`, 템플릿, 테스트용 묶음, 테스트 |
| P3 | 페이지 번호, 한계 안내 문구 | 팀 합의 | 템플릿 |
| P3 | R14 개인정보 탐지 PoC (정규식: 전화번호·주민등록번호·계좌번호·이메일) | 마스킹 정책 결정 | `poc/masking/` |
| P3 | R18 조작 탐지 조사 (EXIF 불일치, 편집 흔적, ELA). "판정"이 아닌 "참고 정보"로만, D는 저장 안 함 | 팀 범위 결정 | 문서만 |

**지시용 프롬프트 초안** (모두 역할 프롬프트 규칙 적용: 코드 작성 전 승인, 명세 기준, D 범위 밖 파일 수정 금지, 새 라이브러리 금지, push는 요청 시만, [작업 결과] 형식 보고)

- **P1-a:** 「`tests/test_d_pdf_renderer.py`에 테스트만 추가한다. ① 저장소 밖 작업 폴더에서 `render_report_pdf` 실행 ② evidence 2건의 인쇄 순서가 받은 순서와 같은지 ③ author, registeredBy, performedBy 값이 인쇄되는지 ④ status 네 가지 값이 글자 그대로 인쇄되는지. 렌더러 코드는 수정하지 않는다. 먼저 테스트 목록을 제시하고 승인 후 작성한다.」
- **P1-b:** 「C 답변 [붙여넣기]에 따라 명세 외 제약 목록 D-1의 각 항목을 유지·완화·삭제한다. 항목별 변경안과 영향받는 테스트를 먼저 표로 제시하고 승인 후 수정한다. 명세에 없는 새 검사는 추가하지 않는다.」
- **P2-a:** 「C·팀이 합의한 R9 표시 방식 [붙여넣기]을 템플릿에 반영한다. 색만으로 구분하지 않고(흑백 인쇄 대비) 글자와 테두리로 구분한다. 변경안과 테스트(불일치, 검증 불가, 미검증 각각)를 먼저 제시한다.」
- **P2-b:** 「명세 v2의 새 필드 [붙여넣기]를 검증과 템플릿에 반영한다. 받은 글자를 그대로 인쇄하고 시각을 재포맷하지 않는다. 변경 파일과 테스트용 묶음 변경안을 먼저 제시한다.」
- **P3-a:** 「`poc/d-masking`에서 OCR 결과에서 전화번호, 주민등록번호, 계좌번호, 이메일을 정규식으로 찾는 PoC를 만든다. 합성 이미지만 쓰고 패키징과는 연결하지 않는다. 정규식 목록과 예상 오탐을 먼저 보고한다.」

## J. 확인하지 못한 것

| 항목 | 이유 |
|---|---|
| 명세 사본과 원본 PDF의 일치 | `docs/specs/`에 원본 PDF 없음 |
| PDF 도구 팀·교수님 결정 | 결정 기록 없음 |
| Docker 이미지 안 실제 렌더링 | 로컬 Docker 빌드 안 함 |
| 대용량 메모리 사용량 | 계산 추정만, 측정 안 함 |
| C의 권한·CoC 구현 | `server/` git grep만. push 안 된 C 코드는 볼 수 없음 |
| 흑백 인쇄 구분 | 상태 시각 구분 표시 자체가 없어 해당 없음 |
| PoC 결과 재실행 | 이번엔 읽기만 함. 결과는 N7 당시 기록. 원본 `rb` 읽기(26행)·`xb` 새 파일 쓰기(92행)는 코드로 확인 |
| N5~N7 단계 번호 | 커밋에 번호가 없어 작업 순서로 짐작 |

점검 후 임시 폴더는 삭제했고 `git status`에 변경이 없는 것을 확인했습니다.
