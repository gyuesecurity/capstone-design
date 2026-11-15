# d_package (D: PDF 보고서 렌더링)

`ReportRenderBundle` v1(dict) → 형식 검증 → Jinja2 템플릿 → WeasyPrint → PDF bytes.

```python
from d_package.pdf_renderer import render_report_pdf, ReportBundleError, PdfRenderError

pdf_bytes = render_report_pdf(bundle)  # 파일을 쓰지 않음
```

- `ReportBundleError`: 묶음 형식 오류 (`e.code`, `e.path`만 담고 입력값은 담지 않음)
- `PdfRenderError`: 템플릿·PDF 도구 실패 (고정 메시지)

## 시스템 의존성 (Pango)

WeasyPrint는 pip 패키지 외에 Pango 라이브러리가 필요합니다.

**Windows**
1. [MSYS2](https://www.msys2.org/) 기본 옵션으로 설치
2. MSYS2 UCRT64 셸에서 `pacman -S mingw-w64-ucrt-x86_64-pango`
3. DLL을 못 찾으면: `set WEASYPRINT_DLL_DIRECTORIES=C:\msys64\ucrt64\bin`
4. `python -m weasyprint --info`로 확인

**Debian/Docker (`python:*-slim`)**
```
apt-get install -y libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0
```

## 폰트

한글 폰트는 `templates/fonts/`에 번들되어 있어 시스템 폰트 설치가 필요 없습니다.
NanumGothic (SIL Open Font License 1.1, `templates/fonts/OFL.txt`), 출처: google/fonts `ofl/nanumgothic`.

PDF 변환 중 읽을 수 있는 파일은 `templates/` 안의 `.ttf`·`.css`뿐입니다 (그 외 file/http/data 요청은 변환 중단).

## 미리보기 (로컬)
```
python -m pip install -r requirements.txt
python -m scripts.preview_report            # output/report_preview.pdf
python -m scripts.preview_report mock_report_bundle_nulls.json
```
