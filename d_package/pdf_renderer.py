import json
import os
import pdfkit
from jinja2 import Environment, FileSystemLoader

def generate_pdf_report(json_bundle_path, template_path, output_pdf_path):
    # ReportRenderBundle 데이터 로드
    with open(json_bundle_path, 'r', encoding='utf-8') as f:
        bundle_data = json.load(f)

    # Jinja2 템플릿 환경 설정 및 렌더링
    env = Environment(loader=FileSystemLoader('.'))
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
        'enable-local-file-access': None
    }

    try:
        # wkhtmltopdf 실행 파일 경로: WKHTMLTOPDF_PATH 환경변수 우선, 없으면 PATH에서 탐색
        path_wkhtmltopdf = os.environ.get('WKHTMLTOPDF_PATH', '')
        config = pdfkit.configuration(wkhtmltopdf=path_wkhtmltopdf)
        
        # HTML 문자열을 PDF 파일로 저장
        pdfkit.from_string(html_content, output_pdf_path, options=options, configuration=config)
        print(f"✅ PDF 보고서 생성 완료: {output_pdf_path}")
        
        return output_pdf_path 
        
    except Exception as e:
        print(f"❌ PDF 생성 실패: {e}")
        raise e

if __name__ == "__main__":
    generate_pdf_report('tests/fixtures/mock_report_bundle.json', 'd_package/templates/report_template.html', 'final_report.pdf')