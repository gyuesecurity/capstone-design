import json
import os

import pdfkit
from jinja2 import Environment, FileSystemLoader


def generate_pdf_report(json_bundle_path, template_path, output_pdf_path):
    # ReportRenderBundle 데이터 로드
    with open(json_bundle_path, encoding='utf-8') as f:
        bundle_data = json.load(f)

    # Jinja2 템플릿 환경 설정 및 렌더링
    # PYSEC-2026-2860 완화: 번들 값의 HTML/스크립트 주입 차단
    env = Environment(loader=FileSystemLoader('.'), autoescape=True)
    template = env.get_template(template_path)
    
    # JSON 데이터를 템플릿 변수로 전달하여 HTML 렌더링
    html_content = template.render(
        reportId=bundle_data['reportId'],
        jobId=bundle_data['jobId'],
        created=bundle_data['created'],
        report=bundle_data['report'],
        evidence=bundle_data['evidence']
    )

    # PDF 변환 옵션 설정 (바닥글 표기 포함)
    options = {
        'page-size': 'A4',
        'margin-top': '20mm',
        'margin-right': '20mm',
        'margin-bottom': '20mm',
        'margin-left': '20mm',
        'encoding': 'UTF-8',
        'footer-center': f"EVIDENCE {bundle_data['reportId']}",
        'footer-font-size': '10',
        'footer-spacing': '5',
        # PYSEC-2026-2860 완화: JS 실행 차단, 로컬 파일 접근 미허용(템플릿이 로컬 리소스 미사용)
        'disable-javascript': None,
    }

    try:
        # wkhtmltopdf 실행 파일 경로: WKHTMLTOPDF_PATH 환경변수 우선, 없으면 PATH에서 탐색
        path_wkhtmltopdf = os.environ.get('WKHTMLTOPDF_PATH', '')
        config = pdfkit.configuration(wkhtmltopdf=path_wkhtmltopdf)
        
        # HTML 문자열을 PDF 파일로 저장
        pdfkit.from_string(html_content, output_pdf_path, options=options, configuration=config)
        # 전체 경로 노출 금지: 파일명만 출력, cp949 콘솔 호환 위해 ASCII 표기
        print(f"[OK] PDF 보고서 생성 완료: {os.path.basename(output_pdf_path)}")
        
        return output_pdf_path 
        
    except Exception as e:
        # 예외 메시지에 경로/내용이 섞일 수 있어 타입만 출력 (상세는 re-raise로 전달)
        print(f"[FAIL] PDF 생성 실패: {type(e).__name__}")
        raise


if __name__ == "__main__":
    generate_pdf_report('tests/fixtures/mock_report_bundle.json', 'd_package/templates/report_template.html', 'final_report.pdf')