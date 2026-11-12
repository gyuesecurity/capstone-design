# d_package (D: PDF 보고서 렌더링)

`ReportRenderBundle` JSON → Jinja2 템플릿 → WeasyPrint → `report.pdf`.

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

한글 폰트는 `fonts/`에 번들되어 있어 시스템 폰트 설치가 필요 없습니다.
NanumGothic (SIL Open Font License 1.1, `fonts/OFL.txt`), 출처: google/fonts `ofl/nanumgothic`.

## 실행
```
python -m pip install -r requirements.txt
python d_package/pdf_renderer.py
```
