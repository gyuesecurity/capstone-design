import json
import os
import re

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from weasyprint.urls import FatalURLFetchingError, URLFetcher

REPORT_ID_PATTERN = re.compile(r'RPT-[0-9a-f]{32}')


class _DenyExternalFetcher(URLFetcher):
    # 템플릿은 외부 리소스를 쓰지 않음: file://, http(s) 등 data: 외 전부 차단해 로컬 파일 유출/SSRF 방지
    def fetch(self, url, headers=None):
        if url.startswith('data:'):
            return super().fetch(url, headers)
        raise FatalURLFetchingError('external resource blocked')


def generate_pdf_report(json_bundle_path, template_path, output_pdf_path):
    # ReportRenderBundle 데이터 로드
    with open(json_bundle_path, encoding='utf-8') as f:
        bundle_data = json.load(f)

    # reportId는 CSS(@page 바닥글)에도 들어가므로 HTML 이스케이프와 별개로 형식 검증
    if not REPORT_ID_PATTERN.fullmatch(str(bundle_data['reportId'])):
        raise ValueError('invalid reportId format')

    # Jinja2 템플릿 환경 설정 및 렌더링 (autoescape: 번들 값의 HTML 주입 차단)
    env = Environment(loader=FileSystemLoader('.'), autoescape=True)
    template = env.get_template(template_path)

    # JSON 데이터를 템플릿 변수로 전달하여 HTML 렌더링 (A4/여백/바닥글은 템플릿 @page에서 지정)
    html_content = template.render(
        reportId=bundle_data['reportId'],
        jobId=bundle_data['jobId'],
        created=bundle_data['created'],
        report=bundle_data['report'],
        evidence=bundle_data['evidence']
    )

    try:
        HTML(string=html_content, url_fetcher=_DenyExternalFetcher()).write_pdf(output_pdf_path)
        # 전체 경로 노출 금지: 파일명만 출력, cp949 콘솔 호환 위해 ASCII 표기
        print(f"[OK] PDF 보고서 생성 완료: {os.path.basename(output_pdf_path)}")

        return output_pdf_path

    # FatalURLFetchingError는 BaseException 상속이라 별도 명시
    except (Exception, FatalURLFetchingError) as e:
        # 예외 메시지에 경로/내용이 섞일 수 있어 타입만 출력 (상세는 re-raise로 전달)
        print(f"[FAIL] PDF 생성 실패: {type(e).__name__}")
        raise


if __name__ == "__main__":
    generate_pdf_report('tests/fixtures/mock_report_bundle.json', 'd_package/templates/report_template.html', 'final_report.pdf')
