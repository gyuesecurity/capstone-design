import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

try:
    from pypdf import PdfReader
    from weasyprint import HTML
    from weasyprint.urls import FatalURLFetchingError

    from d_package.pdf_renderer import _DenyExternalFetcher, generate_pdf_report

    RENDERER_AVAILABLE = True
except (ImportError, OSError):  # OSError: Pango 시스템 라이브러리 없음
    RENDERER_AVAILABLE = False

FIXTURE = Path('tests/fixtures/mock_report_bundle.json')
TEMPLATE = 'd_package/templates/report_template.html'


@unittest.skipUnless(RENDERER_AVAILABLE, 'WeasyPrint/Pango 또는 pypdf 미설치')
class PdfRendererTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.bundle = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.report_id = self.bundle['reportId']

    def render(self):
        bundle_path = self.tmp / 'bundle.json'
        bundle_path.write_text(json.dumps(self.bundle), encoding='utf-8')
        out = self.tmp / 'report.pdf'
        # 렌더러의 [OK]/[FAIL] 콘솔 출력은 테스트 로그에서 숨김
        with contextlib.redirect_stdout(io.StringIO()):
            result = generate_pdf_report(str(bundle_path), TEMPLATE, str(out))
        return result, out

    def page_texts(self, pdf_path):
        return [page.extract_text() for page in PdfReader(pdf_path).pages]

    def test_renders_pdf(self):
        result, out = self.render()
        self.assertEqual(result, str(out))
        self.assertTrue(out.read_bytes().startswith(b'%PDF'))

    def test_report_id_in_page1_body(self):
        _, out = self.render()
        first_page = self.page_texts(out)[0]
        # 바닥글과 별개로 본문에도 있어야 하므로 바닥글 문자열을 제외하고 검사
        body = first_page.replace(f'EVIDENCE {self.report_id}', '')
        self.assertIn(self.report_id, body)

    def test_footer_on_every_page(self):
        self.bundle['evidence'] = self.bundle['evidence'] * 15
        _, out = self.render()
        pages = self.page_texts(out)
        self.assertGreater(len(pages), 1)
        for number, text in enumerate(pages, start=1):
            with self.subTest(page=number):
                self.assertIn(f'EVIDENCE {self.report_id}', text)

    def test_korean_text_extractable(self):
        _, out = self.render()
        self.assertIn('수집된 증거 목록', self.page_texts(out)[0])

    def test_html_injection_escaped(self):
        self.bundle['jobId'] = '<script>alert(1)</script>'
        _, out = self.render()
        self.assertIn('<script>alert(1)</script>', self.page_texts(out)[0])

    def test_external_resource_blocked(self):
        # Jinja 로더가 저장소 밖 템플릿(..)을 거부하므로 렌더러의 fetcher를 직접 검증
        html = HTML(string='<img src="file:///nonexistent/blocked.png">', url_fetcher=_DenyExternalFetcher())
        with self.assertRaises(FatalURLFetchingError):
            html.write_pdf(self.tmp / 'blocked.pdf')

    def test_invalid_report_id_rejected(self):
        self.bundle['reportId'] = 'RPT-x"; } body { display: none'
        with self.assertRaises(ValueError):
            self.render()


if __name__ == '__main__':
    unittest.main()
