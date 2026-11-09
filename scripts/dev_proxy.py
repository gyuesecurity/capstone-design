"""개발용 프록시 서버 (프론트엔드 화면 + 백엔드 API 연결).

하는 일
  1. 프론트엔드 폴더의 화면 파일(HTML, CSS, JS, 이미지)을 브라우저에 보내 줍니다.
  2. 주소가 /api/ 로 시작하는 요청은 백엔드 FastAPI 서버로 그대로 넘기고,
     백엔드의 응답을 그대로 브라우저에 돌려줍니다.

왜 필요한가
  프론트엔드 코드는 fetch("/api/...") 처럼 상대 경로로 API를 부릅니다.
  화면(5500)과 API(8000)를 따로 띄우면 상대 경로 요청이 5500으로 가 버리므로,
  이 프록시가 /api 요청만 8000으로 전달해 줍니다.
  브라우저 입장에서는 화면과 API가 같은 주소(같은 출처)라서 CORS 설정이 필요 없고,
  프론트엔드 코드도 고칠 필요가 없습니다.

주의
  - 개발·시연용입니다. 운영 서버로 쓰지 않습니다.
  - 내 컴퓨터(127.0.0.1)에서만 접속할 수 있게 엽니다.
  - Python 표준 라이브러리만 사용합니다. (새 패키지 설치 없음)

실행 예
  터미널 1 (백엔드):  uvicorn server.main:app --port 8000
  터미널 2 (프록시):  python scripts/dev_proxy.py --frontend ../프론트엔드폴더
  브라우저:           http://127.0.0.1:5500/index.html
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

# 한 번에 주고받는 데이터 크기 (1 MiB). 큰 증거 파일도 나눠서 전달합니다.
CHUNK_SIZE = 1024 * 1024

# 백엔드 서버가 응답하지 않을 때 기다리는 최대 시간(초).
# 보고서 PDF·패키지 ZIP 생성은 오래 걸릴 수 있어 넉넉하게 둡니다.
BACKEND_TIMEOUT = 300

# 프록시가 받아 주는 요청 본문의 최대 크기 (64 MiB).
# 백엔드의 업로드 한도(50 MiB)에 multipart 양식 데이터 여유분을 더한 값입니다.
# 50~64 MiB 요청은 백엔드까지 전달되어 백엔드의 413 안내 문구를 그대로 받습니다.
MAX_BODY_SIZE = 64 * 1024 * 1024

# 연결 자체에 관한 헤더(hop-by-hop 헤더)는 다음 연결로 넘기지 않습니다.
# (HTTP 표준 RFC 9110 7.6.1 기준)
HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
}


def hop_by_hop_names(connection_values: list[str] | None) -> set[str]:
    """기본 hop-by-hop 헤더 + Connection 헤더가 지목한 헤더 이름들을 돌려줍니다.

    예: "Connection: close, X-Foo" 이면 X-Foo 도 이번 연결에서만 쓰는 헤더이므로
    다음 연결로 넘기지 않아야 합니다. (RFC 9110 7.6.1)
    """
    names = set(HOP_BY_HOP_HEADERS)
    for value in connection_values or []:
        names.update(token.strip().lower() for token in value.split(",") if token.strip())
    return names


class DevProxyHandler(SimpleHTTPRequestHandler):
    """요청 하나를 처리하는 클래스.

    /api 로 시작하면 백엔드로 넘기고, 나머지는 프론트엔드 파일을 보내 줍니다.
    파일 전송 기능은 Python 표준 SimpleHTTPRequestHandler를 그대로 사용합니다.
    """

    # 백엔드 주소. main()에서 실행 옵션으로 정합니다.
    backend_host = "127.0.0.1"
    backend_port = 8000

    # 브라우저가 요청을 보내다 멈췄을 때 기다리는 최대 시간(초).
    # (Content-Length 만큼 본문을 보내지 않고 멈추면 이 시간 뒤 연결을 끊습니다.)
    timeout = 60

    # ---- 요청 종류별 진입점 -------------------------------------------------

    def do_GET(self):
        if self._is_api_request():
            self._forward_to_backend()
        else:
            super().do_GET()  # 프론트엔드 파일 보내기

    def do_HEAD(self):
        if self._is_api_request():
            self._forward_to_backend()
        else:
            super().do_HEAD()

    # 아래 요청들은 API에서만 쓰입니다. 화면 파일에는 해당하지 않으므로
    # /api 가 아니면 405(허용하지 않는 방식)로 응답합니다.
    def do_POST(self):
        self._forward_or_reject()

    def do_PUT(self):
        self._forward_or_reject()

    def do_DELETE(self):
        self._forward_or_reject()

    def do_PATCH(self):
        self._forward_or_reject()

    def do_OPTIONS(self):
        self._forward_or_reject()

    # ---- 내부 동작 -----------------------------------------------------------

    def _is_api_request(self) -> bool:
        """주소가 /api 또는 /api/... 인지 확인합니다. (/apix 같은 주소는 제외)"""
        path = urlsplit(self.path).path
        return path == "/api" or path.startswith("/api/")

    def _forward_or_reject(self):
        if self._is_api_request():
            self._forward_to_backend()
        else:
            self.send_error(405, "This method is only allowed for /api requests.")

    def _forward_to_backend(self):
        """요청을 백엔드로 그대로 보내고, 백엔드 응답을 그대로 돌려줍니다."""
        # 1) 요청 본문 크기 확인.
        #    브라우저의 fetch는 본문이 있으면 Content-Length를 보냅니다.
        #    길이를 알 수 없는 방식(chunked)은 개발용 프록시에서 지원하지 않습니다.
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            self._send_json_error(411, "개발용 프록시는 길이를 알 수 없는 요청 본문을 지원하지 않습니다.")
            return
        # Content-Length 는 0~9 숫자로만 된 값 하나만 받습니다.
        # (int() 는 "-5", "+5", "5_0", 전각 숫자도 받아들이므로 직접 검사합니다.)
        # 값이 여러 개이고 서로 다르면 본문 길이를 정할 수 없으므로 거절합니다.
        length_values = {value.strip() for value in self.headers.get_all("Content-Length") or []}
        if len(length_values) > 1:
            self._send_json_error(400, "요청 본문 길이(Content-Length)가 올바르지 않습니다.")
            return
        length_text = length_values.pop() if length_values else None
        if length_text is not None and not (length_text.isascii() and length_text.isdigit()):
            self._send_json_error(400, "요청 본문 길이(Content-Length)가 올바르지 않습니다.")
            return
        body_length = int(length_text) if length_text is not None else 0
        if body_length > MAX_BODY_SIZE:
            self._send_json_error(413, "요청이 너무 큽니다. 증거 파일은 50 MB 이하만 등록할 수 있습니다.")
            return

        # 2) 넘길 헤더 정리: 연결 관련 헤더(Connection 이 지목한 헤더 포함)는 빼고,
        #    Host는 백엔드 주소로 바꾸고, Content-Length 는 검사한 값으로 다시 씁니다.
        #    Authorization(로그인 토큰), Content-Type 등은 그대로 전달됩니다.
        #    같은 이름의 헤더가 여러 개여도 빠지지 않도록 목록으로 보관합니다.
        skip = hop_by_hop_names(self.headers.get_all("Connection")) | {"host", "content-length"}
        headers = [(name, value) for name, value in self.headers.items() if name.lower() not in skip]
        headers.append(("Host", f"{self.backend_host}:{self.backend_port}"))
        if length_text is not None:
            headers.append(("Content-Length", str(body_length)))

        connection = http.client.HTTPConnection(self.backend_host, self.backend_port, timeout=BACKEND_TIMEOUT)
        try:
            # 3) 요청 보내기. 본문은 1 MiB씩 나눠서 그대로 전달합니다(파일 업로드 대비).
            connection.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
            for name, value in headers:
                connection.putheader(name, value)
            connection.endheaders()
            remaining = body_length
            while remaining > 0:
                try:
                    chunk = self.rfile.read(min(CHUNK_SIZE, remaining))
                except OSError:
                    chunk = b""  # 브라우저 쪽 대기 시간 초과 또는 연결 끊김
                if not chunk:
                    # 본문이 덜 왔으므로 백엔드 요청을 버리고 연결을 닫습니다.
                    # (백엔드 오류가 아니므로 502 를 보내지 않습니다.)
                    connection.close()
                    self.close_connection = True
                    return
                connection.send(chunk)
                remaining -= len(chunk)

            # 요청을 끝까지 받았으므로 60초 제한은 여기까지만 적용합니다.
            # (다운로드를 보내는 동안 브라우저가 잠시 멈춰도 연결을 끊지 않습니다.)
            self.connection.settimeout(None)

            # 4) 백엔드 응답 받기
            response = connection.getresponse()
        except OSError:
            # 백엔드가 꺼져 있거나 응답이 없는 경우.
            # 프론트엔드가 보여 줄 수 있게 백엔드와 같은 {"detail": "..."} 형식으로 알려 줍니다.
            connection.close()
            self._send_json_error(
                502, f"API 서버({self.backend_host}:{self.backend_port})에 연결할 수 없습니다. 백엔드 서버가 실행 중인지 확인해 주세요.")
            return

        try:
            # 5) 상태 코드와 헤더를 그대로 돌려줍니다. (401, 404, 422 같은 오류도 그대로)
            self.send_response_only(response.status, response.reason)
            response_skip = hop_by_hop_names(response.msg.get_all("Connection"))
            for name, value in response.getheaders():
                if name.lower() not in response_skip:
                    self.send_header(name, value)
            # 응답이 끝나면 연결을 닫습니다. 응답 길이를 몰라도 끝을 알 수 있게 하기 위해서입니다.
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            # 6) 본문을 1 MiB씩 그대로 전달합니다(파일 다운로드 대비). HEAD 요청은 본문이 없습니다.
            if self.command != "HEAD":
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # 브라우저가 다운로드 도중 창을 닫는 등 연결을 끊은 경우. 개발용이므로 조용히 끝냅니다.
            self.close_connection = True
        finally:
            connection.close()

    def _send_json_error(self, status: int, message: str):
        """프록시 자체 오류를 백엔드와 같은 형식 {"detail": "..."} 으로 보냅니다."""
        body = json.dumps({"detail": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_head(self):
        """화면 파일을 보내기 전에, 실제 위치가 프론트엔드 폴더 안인지 확인합니다.

        심볼릭 링크(Windows는 junction 포함)를 따라간 최종 위치가 폴더 밖이면 404 입니다.
        폴더 주소로 요청하면 그 안의 index.html 도 같은 방법으로 확인합니다.
        """
        root = Path(self.directory).resolve()
        target = Path(self.translate_path(self.path))
        candidates = [target]
        if target.is_dir():
            candidates += [target / name for name in self.index_pages]
        for candidate in candidates:
            if candidate.exists() and not candidate.resolve().is_relative_to(root):
                self.send_error(404, "File not found")
                return None
        return super().send_head()

    def list_directory(self, path):
        """폴더 안의 파일 목록은 보여 주지 않습니다. (index.html이 없는 폴더는 404)"""
        self.send_error(404, "File not found")
        return None

    def end_headers(self):
        # 개발 중 수정한 화면 파일이 바로 보이도록 브라우저 캐시를 쓰지 않게 합니다.
        # (API 응답 헤더는 위에서 백엔드 것을 그대로 쓰고, 이 헤더는 화면 파일에만 붙습니다.)
        if not self._is_api_request():
            self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="개발용 프록시: 프론트엔드 화면 + /api 요청을 백엔드로 전달")
    parser.add_argument("--frontend", required=True,
                        help="프론트엔드 폴더 경로 (index.html이 있는 폴더)")
    parser.add_argument("--port", type=int, default=5500,
                        help="화면을 열 포트 (기본값 5500)")
    parser.add_argument("--backend", default="http://127.0.0.1:8000",
                        help="백엔드 FastAPI 주소 (기본값 http://127.0.0.1:8000)")
    args = parser.parse_args(argv)

    frontend = Path(args.frontend).resolve()
    if not frontend.is_dir():
        print(f"프론트엔드 폴더를 찾을 수 없습니다: {frontend}", file=sys.stderr)
        return 1

    backend = urlsplit(args.backend)
    if backend.scheme != "http" or not backend.hostname or backend.path not in ("", "/"):
        print("--backend 는 http://호스트:포트 형식이어야 합니다. (예: http://127.0.0.1:8000)", file=sys.stderr)
        return 1
    DevProxyHandler.backend_host = backend.hostname
    DevProxyHandler.backend_port = backend.port or 80

    # 내 컴퓨터(127.0.0.1)에서만 접속할 수 있게 엽니다.
    handler = partial(DevProxyHandler, directory=str(frontend))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"화면:  http://127.0.0.1:{args.port}/index.html  (폴더: {frontend})")
    print(f"API:   /api/* 요청은 {args.backend} 로 전달합니다.")
    print("종료하려면 Ctrl+C 를 누르세요.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
