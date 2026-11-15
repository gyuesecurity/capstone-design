"""ReportRenderBundle v1 → PDF bytes (docs/specs/report_render_bundle_v1.md).

파일을 쓰지 않고 결과를 캐시하지 않는다. 출력(print/로그) 없이 예외로만 실패를 알린다.
"""

from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from d_package.errors import PdfRenderError, ReportBundleError
from d_package.report_bundle import validate_report_bundle

__all__ = ['PdfRenderError', 'ReportBundleError', 'render_report_pdf']

TEMPLATE_DIR = (Path(__file__).parent / 'templates').resolve()
TEMPLATE_NAME = 'report_template.html'
ALLOWED_SUFFIXES = {'.ttf', '.css'}

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=True,
    undefined=StrictUndefined,
)


def _is_template_asset(url):
    """templates/ 안의 폰트·CSS 파일(file://, 호스트 없음)만 허용."""
    parsed = urlparse(url)
    if parsed.scheme != 'file' or parsed.netloc:
        return False
    # resolve()로 ../ 와 심볼릭 링크를 풀어 실제 위치를 확인
    path = Path(url2pathname(parsed.path)).resolve()
    return path.is_relative_to(TEMPLATE_DIR) and path.suffix in ALLOWED_SUFFIXES and path.is_file()


def _template_only_fetcher(urls):
    # WeasyPrint 70: URLFetcher 상속 후 fetch 재정의. 일반 예외는 리소스만 건너뛰므로 Fatal로 변환 자체를 중단
    class TemplateOnlyFetcher(urls.URLFetcher):
        def fetch(self, url, headers=None):
            if _is_template_asset(url):
                return super().fetch(url, headers)
            raise urls.FatalURLFetchingError('resource blocked')

    return TemplateOnlyFetcher()


def render_report_pdf(bundle: dict) -> bytes:
    """ReportRenderBundle v1을 받아 PDF 바이트를 돌려준다.

    형식 오류는 ReportBundleError, 템플릿·PDF 도구 실패는 PdfRenderError.
    """
    validate_report_bundle(bundle)

    # 예외 메시지에 입력값이 섞일 수 있어 고정 메시지로 바꾸고 원인 연결을 끊는다
    try:
        html = _env.get_template(TEMPLATE_NAME).render(**bundle)
    except Exception:  # noqa: BLE001 - 모든 실패를 고정 메시지 PdfRenderError로 통일
        raise PdfRenderError('보고서 템플릿 렌더링 실패') from None

    # 함수 안에서 import: Pango 미설치 등으로 실패해도 서버 시작은 막지 않음
    try:
        import weasyprint
        from weasyprint import urls
    except (ImportError, OSError):
        raise PdfRenderError('PDF 도구를 불러올 수 없음') from None

    try:
        return weasyprint.HTML(
            string=html,
            base_url=TEMPLATE_DIR.as_uri() + '/',
            url_fetcher=_template_only_fetcher(urls),
        ).write_pdf()
    # FatalURLFetchingError는 BaseException 상속이라 별도 명시
    except (Exception, urls.FatalURLFetchingError):  # noqa: BLE001 - 위와 같은 이유
        raise PdfRenderError('PDF 변환 실패') from None
