# D 보고서 PDF 도구 결정 자료: WeasyPrint vs fpdf2

> 작성: D 담당 · 2026-11-15 · 대상: 팀, 교수님
> 목적: 설계 문서 기본안(fpdf2)과 현재 구현(WeasyPrint) 중 어느 쪽으로 갈지 결정하기 위한 자료입니다.
> 근거: WeasyPrint는 현재 저장소 구현·테스트·CI 결과 기준입니다. fpdf2는 이 저장소에 설치해 보지 않았으므로 공식 문서 기준이며, 채택하면 설치 후 다시 확인해야 합니다.

## 요약

- **현재 상태:** `d_package/pdf_renderer.py`가 WeasyPrint 70으로 구현돼 있습니다. 테스트 37개가 로컬과 CI 모두에서 통과했고, PR #11로 올라가 있습니다.
- **권장:** WeasyPrint를 유지합니다. 대신 Docker 이미지에 Pango 라이브러리 3개를 추가해야 합니다(아래 4절에 요청문 첨부).
- **결정 필요:** 설계 문서의 기본안(fpdf2)에서 바꾸는 것을 승인할지, 그리고 Dockerfile 변경을 받아들일지입니다.

---

## 1. 현재 선택: WeasyPrint

| 항목 | 내용 |
|---|---|
| 버전 | `weasyprint>=70,<71` (`requirements.txt`), 로컬 확인 버전 70.0 |
| 방식 | Jinja2 HTML 템플릿 + CSS(`@page` 바닥글)를 PDF로 변환 |
| Python 의존성 | cffi, cssselect2, fonttools, Pillow, pydyf, Pyphen, tinycss2, tinyhtml5 (pip이 자동 설치) |
| 시스템 의존성 | **Pango**(텍스트 배치), HarfBuzz(글자 모양 처리). pip으로 설치되지 않아 OS에 따로 설치해야 합니다 |
| 라이선스 | BSD (패키지 메타데이터: `License :: OSI Approved :: BSD License`) |
| 한글 폰트 | 나눔고딕 TTF와 OFL 라이선스 파일을 `d_package/templates/fonts/`에 포함했습니다. 시스템 폰트를 쓰지 않습니다 |

### 1-1. Windows 개발 환경 설치 (MSYS2)

1. [MSYS2](https://www.msys2.org/)를 기본 옵션으로 설치합니다.
2. MSYS2 **UCRT64** 셸에서 `pacman -S mingw-w64-ucrt-x86_64-pango`를 실행합니다.
3. Python이 DLL을 찾지 못하면 환경변수를 설정합니다: `set WEASYPRINT_DLL_DIRECTORIES=C:\msys64\ucrt64\bin`
4. `python -m weasyprint --info`로 확인합니다. D 개발 PC의 결과는 Pango 1.58.2입니다.

Pango가 없으면 D 렌더링 테스트는 자동으로 건너뜁니다. 서버 시작은 막히지 않고, 대신 PDF를 생성하는 시점에 `PdfRenderError`가 납니다.

### 1-2. Docker (`python:3.12-slim`)에 필요한 apt 패키지

```
libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0
```

- 폰트는 저장소에 들어 있으므로 `fonts-nanum`은 필요 없습니다.
- 구체적인 요청문은 4절에 있습니다.

### 1-3. 보안 설정 (구현 완료)

| 위험 | 대응 |
|---|---|
| 사용자 입력(제목·설명 등)에 HTML 태그를 넣는 경우 | Jinja2 `autoescape=True`로 태그가 글자 그대로 인쇄됩니다. 템플릿에 `\|safe`가 없고, `StrictUndefined`도 적용했습니다 |
| PDF 변환 중 서버의 로컬 파일이나 외부 URL을 읽는 경우 (파일 유출, SSRF) | `url_fetcher`를 제한했습니다. `templates/` 폴더 안에 실제로 있는 `.ttf`·`.css` 파일(`file://`, 호스트 없음)만 열 수 있습니다. 그 외 file·http(s)·data·UNC 요청은 변환 자체를 중단시킵니다 |
| 예외 메시지에 입력값이 섞이는 경우 | 실패 시 고정 메시지의 `PdfRenderError`를 내고 원인 연결을 끊습니다(`from None`). print와 로그 출력도 없습니다 |
| 이전 도구의 취약점 | pdfkit/wkhtmltopdf는 개발이 중단된 도구이고 PYSEC-2026-2860(수정 버전 없음)이 있어서 제거했습니다 |

이 항목들은 모두 `tests/test_d_pdf_renderer.py`에서 테스트합니다.

---

## 2. 설계 문서 기본안: fpdf2

| 항목 | 내용 (공식 문서 기준, 저장소에서 미검증) |
|---|---|
| 순수 Python 여부 | **예.** 의존성(fonttools, Pillow, defusedxml)이 모두 pip 패키지이고, OS 라이브러리가 필요 없습니다 |
| 방식 | HTML이 아니라 코드로 그립니다. `add_page()`, `cell()`, `multi_cell()` 같은 API로 위치와 글자를 직접 배치합니다 |
| 한글 폰트 | `add_font()`로 나눔고딕 TTF를 등록하면 유니코드로 출력됩니다. 한글 완성형 음절은 복잡한 글자 조합이 필요 없어 기본 기능으로 충분합니다. 복잡한 조합이 필요한 문자는 선택 의존성 `uharfbuzz`가 있어야 합니다 |
| HTML 템플릿 재사용 | **사실상 불가.** `write_html()`은 일부 태그만 지원하고 CSS가 거의 적용되지 않으며, `@page` 바닥글도 없습니다. 현재 템플릿의 레이아웃을 코드로 다시 작성해야 합니다. 바닥글은 `FPDF.footer()`를 재정의해 구현합니다 |
| 라이선스 | **LGPL-3.0.** 라이브러리로 import해서 쓰는 것은 일반적으로 문제가 없지만, fpdf2 자체를 수정해 배포하면 수정본을 공개할 의무가 있습니다 |
| 텍스트 추출 | TTF를 넣으면 ToUnicode 정보가 포함돼 글자 추출이 가능한 것으로 문서화돼 있습니다. 채택하면 보고서 ID 추출 테스트로 다시 확인해야 합니다 |
| 보안 | HTML과 URL을 처리하지 않으므로 마크업 주입이나 리소스 요청의 위험이 구조적으로 작습니다. 단 `write_html()`의 `<img src>`는 로컬 파일과 URL을 읽을 수 있으므로 쓰지 않아야 합니다 |

### 2-1. fpdf2로 바꿀 때의 재작업 범위

| 대상 | 변경 |
|---|---|
| `d_package/pdf_renderer.py` | 렌더링 부분을 fpdf2 배치 코드로 새로 작성 (레이아웃, 페이지 넘김, 바닥글, 줄바꿈 없는 해시 줄). `url_fetcher`는 필요 없어져 제거 |
| `d_package/templates/report_template.html` | 삭제 (레이아웃이 코드로 옮겨짐) |
| `d_package/templates/fonts/` | 유지 (위치는 조정할 수 있음) |
| `tests/test_d_pdf_renderer.py` | 바깥에서 보이는 결과를 검사하는 테스트(ID, 해시, options, 미기록, KST)는 대부분 그대로 씀. WeasyPrint 전용 테스트(fetcher, import 실패)는 교체 |
| `requirements.txt` | `weasyprint`를 `fpdf2`로 교체. `jinja2`는 다른 곳에서 쓰지 않으면 제거 |
| 변경 없음 | `report_bundle.py`(형식 검증), `errors.py`, `package_builder.py`(ZIP), 공개 함수 형태 `render_report_pdf(bundle) -> bytes`. C의 호출 코드에는 영향이 없습니다 |

---

## 3. 비교표

| 기준 | WeasyPrint (현재) | fpdf2 (설계 기본안) |
|---|---|---|
| 보안 | HTML 엔진이라 주입·리소스 요청 위험이 있지만 **대응을 구현하고 테스트까지 마침** (autoescape, url_fetcher 제한) | HTML과 URL을 처리하지 않아 **위험 자체가 작음** (`write_html`의 img는 쓰지 말아야 함) |
| 설치·배포 난이도 | OS 라이브러리(Pango)가 필요함. Windows는 MSYS2, Docker는 apt 패키지 3개 추가 | **pip만으로 끝남.** Windows·Docker 추가 설치 없음 |
| 한글 렌더링 | 번들 나눔고딕으로 확인 완료. CI(Ubuntu)에서도 추출 테스트 통과 | 번들 TTF 등록으로 가능 (미검증) |
| 텍스트 추출 (보고서 ID 검사) | 1쪽 본문과 모든 쪽 바닥글의 `EVIDENCE RPT-...`, 64자 해시 추출 테스트 통과 | 가능한 것으로 문서화됨. 재검증 필요 |
| 템플릿 유지보수 | HTML/CSS라 문구와 레이아웃을 고치기 쉬움. 화면(A) 쪽과 같은 지식으로 수정 가능 | 좌표와 줄 높이를 코드로 관리해야 함. 표나 긴 글의 페이지 넘김을 직접 처리해야 함 |
| 팀 코드 영향 | **Dockerfile 변경 필요** (apt 한 줄). CI runner(ubuntu-24.04)는 추가 설정 없이 통과 | **Dockerfile 변경 불필요.** requirements 한 줄만 교체 |
| 남은 작업량 | 없음 (구현·테스트·PR 완료). Dockerfile 반영만 남음 | 렌더러 재작성과 테스트 조정 필요 |
| 라이선스 | BSD (제약 적음) | LGPL-3.0 (수정해서 배포할 때만 공개 의무) |

> **주의:** 지금 CI의 Docker 빌드 단계는 Pango가 없어도 통과합니다. D가 weasyprint를 함수 안에서 import하기 때문입니다. 그래서 Dockerfile에 Pango를 추가하지 않으면 **배포한 서버에서 PDF를 만드는 순간에야 실패가 드러납니다.** WeasyPrint를 유지한다면 4절의 Dockerfile 변경이 꼭 필요합니다.

---

## 4. Dockerfile 담당에게 보낼 변경 요청문 (그대로 붙여 쓰기)

D는 Dockerfile을 직접 수정하지 않습니다. WeasyPrint를 유지하기로 결정하면 아래 요청문을 그대로 보내면 됩니다.

````markdown
**[D 요청] Dockerfile에 WeasyPrint 시스템 라이브러리 추가**

D의 보고서 PDF 생성(`d_package/pdf_renderer.py`, WeasyPrint 70)에 Pango가 필요합니다.
`FROM python:3.12-slim` 바로 다음, `pip install`보다 앞에 아래 한 단계를 넣어 주세요.

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
    && rm -rf /var/lib/apt/lists/*
```

- 한글 폰트는 저장소(`d_package/templates/fonts/`)에 들어 있어서 `fonts-nanum`은 필요 없습니다.
- 빠뜨려도 이미지 빌드와 서버 시작은 성공하고, PDF를 생성할 때 `PdfRenderError`가 납니다. 그래서 CI로는 잡히지 않습니다.
- 확인 방법: `docker run --rm <이미지> python -m weasyprint --info` 출력에 Pango 버전이 나오면 정상입니다.
- 이미지 크기가 늘어나므로 반영 후 측정이 필요합니다.
````

---

## 5. 권장안과 결정이 필요한 항목

### 권장안: WeasyPrint 유지

1. 구현, 보안 대응, 테스트, CI 통과, PR까지 끝나 있어서 **추가 개발이 없습니다.** fpdf2로 바꾸면 렌더러를 다시 쓰고 검증도 처음부터 해야 합니다.
2. HTML/CSS 템플릿이라 보고서 문구와 레이아웃을 바꾸기 쉽습니다. 명세 변경(구역 추가 등)에도 대응하기 쉽습니다.
3. 대가는 Dockerfile 한 단계 추가와 Windows 개발 PC의 MSYS2 설치입니다. Windows 설치는 D 개발 PC에서 이미 해 봤고, 절차는 1-1절과 `d_package/README.md`에 있습니다.
4. 공개 함수 형태가 같아서, 나중에 fpdf2로 바꾸게 되더라도 C의 코드에는 영향이 없습니다.

**fpdf2가 더 나은 경우:** Dockerfile이나 배포 환경에 OS 패키지를 추가할 수 없거나, 팀원 모두가 Windows에서 Pango 없이 렌더링 테스트까지 돌려야 한다면 fpdf2가 유리합니다.

### 결정이 필요한 항목

| # | 항목 | 결정 주체 | 선택지 |
|---|---|---|---|
| 1 | 설계 문서의 기본안(fpdf2)을 WeasyPrint로 바꾸는 것을 승인할지 | 팀, 교수님 | 승인 / fpdf2로 재작업 |
| 2 | Dockerfile에 Pango apt 패키지를 추가할지 (4절) | Dockerfile 담당 (C) | 반영 / 거부(→ fpdf2) |
| 3 | 라이선스 확인 | 팀 | WeasyPrint BSD, 나눔고딕 OFL 1.1 (fpdf2를 택하면 LGPL-3.0) |
| 4 | 다른 팀원도 Windows에서 PDF 렌더링 테스트를 돌려야 하는지 | 팀 | 필요(→ MSYS2 설치 안내) / D만 실행(→ 다른 팀원 PC에서는 자동 건너뜀) |
| 5 | (1에서 WeasyPrint 승인 시) 설계 문서의 PDF 도구 항목을 갱신할 담당 | 팀 | — |
