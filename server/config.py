"""서버 전체에서 함께 쓰는 설정 값.

기존 server/main.py 맨 위에 있던 상수를 그대로 옮겨 왔습니다. (값 변경 없음)
"""

from __future__ import annotations

import re
from pathlib import Path

# 저장소 최상위 폴더. EVIDENCE_DATA_DIR가 없을 때 기본 저장 폴더(ROOT/data)를 정하는 데 씁니다.
ROOT = Path(__file__).resolve().parent.parent
# 증거 파일 최대 크기 (50 MiB)
MAX_FILE_SIZE = 50 * 1024 * 1024
# 파일을 읽고 쓸 때 한 번에 처리하는 크기 (1 MiB)
CHUNK_SIZE = 1024 * 1024
# 등록을 허용하는 파일 확장자
ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".txt", ".csv",
    ".html", ".htm", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".mp4", ".mov", ".webm", ".mp3", ".wav", ".m4a",
}
# 검증 상태 코드 → 화면에 보여 줄 한글 이름
STATUS_LABELS = {
    "NOT_VERIFIED": "미검증", "MATCH": "일치", "MISMATCH": "불일치", "UNAVAILABLE": "검증 불가"
}
# 이메일 형식 검사 (간단한 형태만 확인)
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
