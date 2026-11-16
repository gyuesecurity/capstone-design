"""보안 응답 헤더 (B 코드 feature/evidence-crypto의 security_headers 미들웨어를 옮겨 옴)

모든 응답에 붙이는 헤더 (B 코드와 같은 값)
- X-Content-Type-Options: nosniff        브라우저가 파일 종류를 멋대로 추측해 실행하지 않게 함
- X-Frame-Options: DENY                  다른 사이트가 이 화면을 iframe으로 감싸지 못하게 함
- Referrer-Policy: no-referrer           다른 사이트로 이동할 때 주소를 넘기지 않음
- Permissions-Policy                     카메라·마이크·위치 사용 안 함
- Content-Security-Policy                이 서버의 파일만 실행·불러오기
- Strict-Transport-Security              HTTPS로 접속했을 때만
- Cache-Control: no-store                /api/ 응답은 브라우저에 저장하지 않음

B 코드에서 바꾼 점 (1곳)
- /docs·/redoc(API 문서 화면)에는 CSP를 붙이지 않습니다. 이 화면은 외부 CDN의 스크립트를 쓰는데,
  CSP가 붙으면 빈 화면이 됩니다. (README에 "/docs에서 API 문서를 볼 수 있다"고 안내하고 있음)

FastAPI의 @app.middleware("http") 대신 ASGI 미들웨어로 만들었습니다. 파일 다운로드(스트리밍) 응답을
중간에서 모으지 않고 헤더만 덧붙이기 위해서입니다.
"""

from __future__ import annotations

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
)
COMMON_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
)
DOCS_PATHS = ("/docs", "/redoc")  # /docs/oauth2-redirect 포함


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        extra = list(COMMON_HEADERS)
        if not path.startswith(DOCS_PATHS):
            extra.append((b"content-security-policy", CONTENT_SECURITY_POLICY.encode()))
        if scope.get("scheme") == "https":
            extra.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
        if path.startswith("/api/"):
            extra.append((b"cache-control", b"no-store"))

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                names = {name for name, _ in extra}
                # 같은 이름이 이미 있으면 보안 헤더 값으로 바꿉니다. (중복 헤더 방지)
                headers = [(name, value) for name, value in message.get("headers", []) if name.lower() not in names]
                message = {**message, "headers": headers + extra}
            await send(message)

        await self.app(scope, receive, send_with_headers)
