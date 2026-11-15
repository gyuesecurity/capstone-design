class ReportBundleError(ValueError):
    """ReportRenderBundle 형식 오류. 입력값 노출 방지를 위해 오류 코드와 필드 경로만 담는다."""

    def __init__(self, code, path):
        self.code = code
        self.path = path
        super().__init__(f'{code}: {path}')


class PdfRenderError(RuntimeError):
    """PDF 도구 실행 실패 (도구 없음, 변환 오류 등)"""
