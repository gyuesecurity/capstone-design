import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from d_package import pdf_renderer
from d_package.pdf_renderer import PdfRenderError, ReportBundleError, render_report_pdf

try:
    import weasyprint
    from pypdf import PdfReader
    from weasyprint import urls

    RENDERER_AVAILABLE = True
except (ImportError, OSError):  # OSError: Pango 시스템 라이브러리 없음
    RENDERER_AVAILABLE = False

FIXTURES = Path(__file__).parent / 'fixtures'
FONT_DIR = pdf_renderer.TEMPLATE_DIR / 'fonts'
SECRET = 'SECRET-7f3a'

# 라벨 → 그 구역을 인쇄하게 하는 options
SECTION_LABELS = {
    '증거 이름': {'basic'},
    '등록 일시': {'basic', 'custody'},
    '수집 출처': {'metadata'},
    '기준 SHA-256': {'hash'},
    '검증 상태': {'results'},
    '검증 기록': {'results', 'custody'},
    '관리 이력 기준점': {'custody'},
}


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def page_texts(pdf):
    return [page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages]


def flat(text):
    return ' '.join(text.split())


def template_html(bundle):
    return pdf_renderer._env.get_template('report_template.html').render(**bundle)


PAGE_MARGIN_PX = 20 / 25.4 * 96  # @page margin 20mm


def overflowing_text(bundle):
    """본문 오른쪽 경계를 넘는 글자 상자 (WeasyPrint 레이아웃 내부 값, weasyprint<71 고정 전제)."""
    doc = weasyprint.HTML(
        string=template_html(bundle), base_url=pdf_renderer.TEMPLATE_DIR.as_uri() + '/',
        url_fetcher=pdf_renderer._template_only_fetcher(urls),
    ).render()
    over = []

    def walk(box, limit):
        text = getattr(box, 'text', None)
        if text and text.strip() and box.position_x + box.width > limit + 0.5:
            over.append(text[:20])
        for child in getattr(box, 'children', None) or ():
            walk(child, limit)

    for page in doc.pages:
        walk(page._page_box, page.width - PAGE_MARGIN_PX)
    return over


class TemplateAssetRuleTests(unittest.TestCase):
    """url_fetcher 허용 규칙 (WeasyPrint 없이도 실행)."""

    def test_allows_bundled_font(self):
        self.assertTrue(pdf_renderer._is_template_asset((FONT_DIR / 'NanumGothic-Regular.ttf').as_uri()))

    def test_rejects_other_resources(self):
        rejected = {
            'templates 밖으로 ../': (FONT_DIR / '..' / '..' / 'pdf_renderer.py').as_uri(),
            'd_package 안 다른 파일': (pdf_renderer.TEMPLATE_DIR.parent / 'errors.py').as_uri(),
            'templates 안 html': (pdf_renderer.TEMPLATE_DIR / 'report_template.html').as_uri(),
            '없는 폰트': (FONT_DIR / 'missing.ttf').as_uri(),
            'UNC 호스트': 'file://evil-host/share/x.ttf',
            'http': 'http://127.0.0.1:9/x.css',
            'https': 'https://example.invalid/x.ttf',
            'data': 'data:text/css,body{}',
        }
        for desc, url in rejected.items():
            with self.subTest(desc):
                self.assertFalse(pdf_renderer._is_template_asset(url))


class RenderErrorTests(unittest.TestCase):
    def test_invalid_bundle_raises_bundle_error(self):
        bundle = load('mock_report_bundle.json')
        bundle['reportId'] = 'RPT-x"; } body { display: none'
        with self.assertRaises(ReportBundleError):
            render_report_pdf(bundle)

    def test_weasyprint_import_failure(self):
        with mock.patch.dict(sys.modules, {'weasyprint': None, 'weasyprint.urls': None}), \
                self.assertRaises(PdfRenderError) as ctx:
            render_report_pdf(load('mock_report_bundle.json'))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)


@unittest.skipUnless(RENDERER_AVAILABLE, 'WeasyPrint/Pango 또는 pypdf 미설치')
class PdfRendererTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load('mock_report_bundle.json')
        self.report_id = self.bundle['reportId']

    def test_returns_pdf_bytes(self):
        pdf = render_report_pdf(self.bundle)
        self.assertIsInstance(pdf, bytes)
        self.assertTrue(pdf.startswith(b'%PDF-'))

    def test_report_id_in_page1_body_and_footer(self):
        first_page = page_texts(render_report_pdf(self.bundle))[0]
        # 본문 1회 + 바닥글 1회
        self.assertGreaterEqual(first_page.count(f'EVIDENCE {self.report_id}'), 2)

    def test_footer_on_every_page(self):
        self.bundle['evidence'] = self.bundle['evidence'] * 15
        pages = page_texts(render_report_pdf(self.bundle))
        self.assertGreater(len(pages), 1)
        for number, text in enumerate(pages, start=1):
            with self.subTest(page=number):
                self.assertIn(f'EVIDENCE {self.report_id}', text)

    def test_hashes_unbroken(self):
        text = page_texts(render_report_pdf(self.bundle))[0]
        item = self.bundle['evidence'][0]
        for value in (item['sha256'], item['verification']['currentHash'], item['custodySnapshot']['eventHash']):
            self.assertIn(value, text)
        seq = item['custodySnapshot']['sequenceNo']
        self.assertIn(f'관리 이력 기준점: {seq} · {item["custodySnapshot"]["eventHash"]}', text)

    def test_time_zone_and_korean_text(self):
        text = page_texts(render_report_pdf(self.bundle))[0]
        item = self.bundle['evidence'][0]
        for value in (self.bundle['created'], item['registeredAt'], item['verification']['completedAt']):
            self.assertIn(f'{value} (KST)', text)
        self.assertIn('수집된 증거 목록', text)

    def test_sections_follow_options(self):
        for options in ([], ['basic'], ['metadata'], ['hash'], ['results'], ['custody']):
            with self.subTest(options=options):
                bundle = copy.deepcopy(self.bundle)
                bundle['report']['options'] = options
                text = '\n'.join(page_texts(render_report_pdf(bundle)))
                item = bundle['evidence'][0]
                self.assertIn(item['id'], text)  # 항상 인쇄
                self.assertIn(item['filename'], text)
                for label, enabling in SECTION_LABELS.items():
                    if enabling & set(options):
                        self.assertIn(label, text)
                    else:
                        self.assertNotIn(label, text)

    def test_nulls_shown_as_unrecorded(self):
        bundle = load('mock_report_bundle_nulls.json')
        text = flat('\n'.join(page_texts(render_report_pdf(bundle))))
        # project·description·author, 증거1 registeredBy·source·mime·verification, 증거2 performedBy·currentHash·reason
        # (제목 자체에 들어 있는 '미기록'은 제외)
        self.assertEqual(text.count('미기록') - bundle['report']['title'].count('미기록'), 10)
        self.assertIn('검증 기록: 미기록', text)
        self.assertNotIn('None', text)

    def test_markup_inputs_printed_literally_without_fetch(self):
        self.bundle['report']['title'] = '<script>alert(1)</script>'
        self.bundle['report']['project'] = '<img src="file:///C:/Windows/win.ini">'
        self.bundle['report']['description'] = '<link rel="stylesheet" href="http://127.0.0.1:9/x.css">'
        with mock.patch.object(pdf_renderer, '_is_template_asset', wraps=pdf_renderer._is_template_asset) as rule:
            text = flat(page_texts(render_report_pdf(self.bundle))[0])
        for value in (self.bundle['report']['title'], self.bundle['report']['project'],
                      self.bundle['report']['description']):
            self.assertIn(flat(value), text)
        requested = [call.args[0] for call in rule.call_args_list]
        self.assertTrue(requested)  # 번들 폰트는 요청됨
        for url in requested:
            with self.subTest(url=url):
                self.assertTrue(pdf_renderer._is_template_asset(url))
                self.assertTrue(url.endswith('.ttf'))

    def test_blocked_resource_aborts_render(self):
        fetcher = pdf_renderer._template_only_fetcher(urls)
        with self.assertRaises(urls.FatalURLFetchingError):
            fetcher.fetch('http://127.0.0.1:9/x.css')

    def test_same_bundle_twice(self):
        first = render_report_pdf(self.bundle)
        second = render_report_pdf(self.bundle)
        for pdf in (first, second):
            self.assertTrue(pdf.startswith(b'%PDF-'))
            self.assertIn(f'EVIDENCE {self.report_id}', page_texts(pdf)[0])

    def test_long_values_wrap_inside_page(self):
        item = self.bundle['evidence'][0]
        item['name'] = '가' * 100 + 'A' * 100  # 200자, 공백 없음
        item['filename'] = 'x' * 150 + '.png'
        item['verification']['reason'] = '암호화 파일 또는 인증 태그가 변경되어 AES-GCM 인증에 실패했습니다. ' * 2
        self.assertEqual(overflowing_text(self.bundle), [])
        text = flat(''.join(page_texts(render_report_pdf(self.bundle))))
        self.assertIn(item['sha256'], text)  # 해시는 여전히 한 줄

    def test_renders_from_outside_repo_cwd(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as outside:
            os.chdir(outside)
            try:
                pdf = render_report_pdf(self.bundle)
            finally:
                os.chdir(cwd)
        self.assertTrue(pdf.startswith(b'%PDF-'))

    def test_evidence_printed_in_given_order(self):
        first = self.bundle['evidence'][0]
        second = copy.deepcopy(first)
        second['id'] = 'EV-' + '9' * 32
        for order in ([first, second], [second, first]):
            with self.subTest(first=order[0]['id']):
                self.bundle['evidence'] = order
                text = '\n'.join(page_texts(render_report_pdf(self.bundle)))
                self.assertLess(text.index(order[0]['id']), text.index(order[1]['id']))

    def test_people_values_printed(self):
        self.bundle['report']['author'] = '작성자갑'
        item = self.bundle['evidence'][0]
        item['registeredBy'] = '등록자을'
        item['verification']['performedBy'] = '검증자병'
        text = '\n'.join(page_texts(render_report_pdf(self.bundle)))
        for value in ('작성자갑', '등록자을', '검증자병'):
            self.assertIn(value, text)

    def test_status_text_printed_as_given_and_flagged_without_color(self):
        for status in ('일치', '불일치', '검증 불가', '미검증'):
            with self.subTest(status=status):
                self.bundle['evidence'][0]['status'] = status
                text = page_texts(render_report_pdf(self.bundle))[0]
                self.assertIn(f'검증 상태 {status}', flat(text))
                # 일치가 아니면 굵은 글자·테두리(색 없음) 클래스
                html = template_html(self.bundle)
                flagged = f'<span class="status status-flag">{status}</span>' in html
                self.assertEqual(flagged, status != '일치')

    def test_conversion_failure_raises_render_error(self):
        with mock.patch('weasyprint.HTML.write_pdf', side_effect=RuntimeError(SECRET)), \
                self.assertRaises(PdfRenderError) as ctx:
            render_report_pdf(self.bundle)
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)


if __name__ == '__main__':
    unittest.main()
