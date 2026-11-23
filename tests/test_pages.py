"""화면 파일 제공 테스트 (11/22, Page 4는 11/23): 서버 하나로 화면 두 버전과 API를 같은 주소에서 쓰는지

실제 비밀번호·키는 쓰지 않습니다. 모든 데이터는 .test-data/ 아래 임시 폴더에 만들고 끝나면 지웁니다.
"""

import os

# 키가 없으면 서버가 시작하지 않으므로, 테스트에서는 임시 데이터 폴더에 개발용 키를 자동 생성합니다. (키 값은 저장소에 없음)
os.environ.setdefault("EVIDENCE_ALLOW_DEV_KEY_GENERATION", "true")

import tempfile
import unittest
from pathlib import Path

import httpx

from server import main
from server.web_files import ROOT, public_files

PREVIOUS = ROOT / "versions" / "previous"
PAGE4 = ROOT / "versions" / "page4"


class PageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Path(".test-data").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=".test-data")
        self.app = main.create_app(self.temporary.name)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temporary.cleanup()

    async def test_pages_keep_old_urls(self):
        response = await self.client.get("/")
        self.assertEqual((response.status_code, response.headers["location"]), (307, "/index.html"))
        expected = {
            "index.html": PREVIOUS / "home" / "index.html",
            "evidence.html": PREVIOUS / "evidence-management" / "evidence.html",
            "report_detail.html": PREVIOUS / "reports" / "report_detail.html",
            "report-packages.js": PREVIOUS / "reports" / "report-packages.js",
            "app.css": PREVIOUS / "shared" / "app.css",
        }
        for url, source in expected.items():
            response = await self.client.get("/" + url)
            self.assertEqual(response.status_code, 200, url)
            self.assertEqual(response.content, source.read_bytes(), url)
        # 자산 폴더 (예전 주소 assets/auth/…, assets/report/…)
        asset = next(url for url in public_files() if url.startswith("assets/auth/"))
        self.assertEqual((await self.client.get("/" + asset)).status_code, 200)

    async def test_every_listed_page_is_served(self):
        for prefix, version in (("", "previous"), ("page4/", "page4")):
            files = public_files(version)
            self.assertGreaterEqual(sum(url.endswith(".html") for url in files), 29, version)  # 화면 29개씩
            for url in files:
                self.assertEqual((await self.client.get("/" + prefix + url)).status_code, 200, prefix + url)

    async def test_page4_lives_under_its_own_prefix(self):
        for path in ("/page4", "/page4/"):
            response = await self.client.get(path)
            self.assertEqual((response.status_code, response.headers["location"]), (307, "/page4/index.html"))
        # 같은 주소 이름이라도 버전마다 자기 폴더의 파일을 보냄
        for url, feature in (("index.html", "home"), ("report_detail.html", "reports"), ("app.js", "shared")):
            self.assertEqual((await self.client.get("/page4/" + url)).content, (PAGE4 / feature / url).read_bytes())
            self.assertEqual((await self.client.get("/" + url)).content, (PREVIOUS / feature / url).read_bytes())
        # Page 4 전용 파일은 /page4/ 아래에만 있음
        self.assertEqual((await self.client.get("/page4/assets/brand/credoc-wordmark.svg")).status_code, 200)
        self.assertEqual((await self.client.get("/assets/brand/credoc-wordmark.svg")).status_code, 404)

    async def test_only_listed_files(self):
        for path in ("/server/main.py", "/README.md", "/versions/previous/home/index.html", "/page4/README.md",
                     "/page4/server/main.py", "/page4/../server/main.py", "/page4/deploy/page4-netlify.zip",
                     "/../server/main.py", "/%2e%2e/server/main.py", "/nothing.html", "/data/evidence.sqlite3"):
            self.assertEqual((await self.client.get(path)).status_code, 404, path)

    async def test_api_and_docs_still_work(self):
        self.assertEqual((await self.client.get("/api/health")).json()["backend"], True)
        missing = await self.client.get("/api/nothing")
        self.assertEqual((missing.status_code, missing.headers["content-type"]), (404, "application/json"))
        self.assertEqual((await self.client.get("/docs")).status_code, 200)
        self.assertEqual((await self.client.get("/openapi.json")).status_code, 200)
        self.assertNotIn("/{file_path}", (await self.client.get("/openapi.json")).json()["paths"])  # 화면 주소는 API 문서에 없음

    async def test_pages_get_security_headers(self):
        response = await self.client.get("/index.html")
        policy = response.headers["content-security-policy"]
        self.assertIn("default-src 'self'", policy)
        self.assertIn("frame-src 'self' blob:", policy)  # 보고서 미리보기 PDF(blob:) iframe
        self.assertIn("font-src 'self' https://fonts.gstatic.com", policy)  # report.css 글꼴 (A가 로컬 글꼴을 넣으면 제거)
        self.assertIn("script-src 'self';", policy)  # 스크립트는 여전히 이 서버 것만
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertNotIn("cache-control", response.headers)  # no-store는 /api/만


if __name__ == "__main__":
    unittest.main()
