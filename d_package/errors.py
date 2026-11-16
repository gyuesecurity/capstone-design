class _FieldError(ValueError):
    """입력값 노출 방지를 위해 오류 코드와 필드 위치(번호 기반)만 담는다."""

    def __init__(self, code, path):
        self.code = code
        self.path = path
        super().__init__(f'{code}: {path}')


class ReportBundleError(_FieldError):
    """ReportRenderBundle 형식 오류."""


class PdfRenderError(RuntimeError):
    """PDF 도구 실행 실패 (도구 없음, 변환 오류 등)"""


class PackageBundleError(_FieldError):
    """PackageBuildBundle 형식 오류 (필수 필드 없음, 경로 중복, size·sha256이 data와 다름 등)"""


class PackageBuildError(RuntimeError):
    """ZIP을 만드는 중 실패"""
