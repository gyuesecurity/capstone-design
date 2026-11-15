"""로컬 미리보기: mock 묶음으로 output/report_preview.pdf를 만든다.

실행(저장소 루트): python -m scripts.preview_report [fixture 파일명]
"""

import json
import sys
from pathlib import Path

from d_package.pdf_renderer import render_report_pdf

ROOT = Path(__file__).resolve().parent.parent


def main(fixture='mock_report_bundle.json'):
    # 파일명만 받아 tests/fixtures 밖은 읽지 않음
    source = ROOT / 'tests' / 'fixtures' / Path(fixture).name
    bundle = json.loads(source.read_text(encoding='utf-8'))
    out = ROOT / 'output' / 'report_preview.pdf'
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(render_report_pdf(bundle))
    print('output/report_preview.pdf')


if __name__ == '__main__':
    main(*sys.argv[1:2])
