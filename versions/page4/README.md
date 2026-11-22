# CredOc · Page 4 웹 버전

Figma Page 4의 서비스 소개, 로그인, 증거 등록·관리, 무결성 검증, 보고서, 내 계정 화면을 웹에 적용했습니다. 소스는 기능별 폴더에 있고 공통 스타일과 스크립트는 `shared/`에 있습니다.

**서버로 사용하기** — 프로젝트 루트에서 `start-server.cmd`를 실행한 뒤 [Page 4](http://127.0.0.1:8000/page4/)를 여세요. 기존 버전과 같은 계정·증거·검증 이력을 사용합니다. [설치 방법](../../README.md#windows-로컬-실행)

**정적 시연** — [Page 4 시연 ZIP](deploy/page4-netlify.zip)의 압축을 풀고 해당 폴더를 정적 웹 서버에서 여세요. 로그인 화면에서 **로그인 없이 시연 화면 보기**를 누르면 등록한 파일은 해당 브라우저에 저장됩니다. 직접 내보낼 때는 프로젝트 루트에서 다음을 실행하세요.

```powershell
python -m server.web_files page4
python -m http.server 8080 --directory .build/page4
```

정적 시연은 브라우저에서 보고서 PDF를 생성합니다. 서버에 보고서를 저장하거나 증거 패키지를 받으려면 보고서·패키지 API가 필요합니다. 동봉한 서버의 지원 범위는 [프로젝트 README](../../README.md#주요-기능)에서 확인할 수 있습니다.
