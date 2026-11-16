"""업로드 파일 형식 확인 (B 코드 feature/evidence-crypto의 file_signature_matches를 옮겨 옴)

브라우저가 보낸 MIME 값을 믿지 않고, 파일 앞부분(최대 SNIFF_SIZE)을 직접 보고 확장자와 실제 형식이 맞는지 확인합니다.
예: 실행 파일 이름을 "사진.png"로 바꿔 올리면 PNG 서명이 없어서 거부됩니다.

B 코드에서 바꾼 점 (1곳)
- 텍스트 파일(.txt .csv .html .htm): 앞부분 8 KiB를 자르면 한글 한 글자(UTF-8 3바이트, CP949 2바이트)가
  중간에서 잘릴 수 있습니다. B 코드는 이때 정상 파일도 거부해서, 8 KiB가 넘는 한글 텍스트 약 3개 중 1개가 등록되지 않았습니다.
  그래서 잘라 낸 앞부분일 때는 "끝에서 잘린 글자"만 허용하고, 그 밖의 잘못된 바이트는 그대로 거부합니다.

주의: 이 검사는 확장자 위장을 줄이는 것이지 악성코드 판정이 아닙니다.
"""

from __future__ import annotations

import codecs
import zipfile
from typing import BinaryIO

SNIFF_SIZE = 8192  # 형식 확인에 읽는 앞부분 크기 (B 코드와 같은 값)


def _text_header_ok(header: bytes, truncated: bool) -> bool:
    """텍스트 파일 앞부분: NUL 없음 + UTF-8 또는 CP949로 읽힘. 잘라 낸 앞부분이면 끝의 덜 끝난 글자는 허용"""
    if b"\x00" in header:
        return False
    for encoding in ("utf-8", "cp949"):
        try:
            # final=False: 끝에서 잘린 글자는 오류로 보지 않고 다음 입력을 기다리는 것으로 처리 (중간의 잘못된 바이트는 오류)
            codecs.getincrementaldecoder(encoding)().decode(header, final=not truncated)
            return True
        except UnicodeDecodeError:
            continue
    return False


def file_signature_matches(extension: str, header: bytes, file_object: BinaryIO, truncated: bool = False) -> bool:
    """확장자와 실제 파일 형식이 맞으면 True (B 코드 그대로, 텍스트만 위 설명대로 보완)

    header: 파일 앞부분. truncated: 파일이 header보다 길면 True (앞부분만 잘라 왔다는 뜻)
    file_object: ZIP 계열(.zip .docx .xlsx)은 목록을 열어 봐야 해서 파일 전체가 필요합니다. 끝나면 처음 위치로 돌려놓습니다.
    """
    if extension == ".pdf":
        return header.startswith(b"%PDF-")
    if extension in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    if extension == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    if extension == ".wav":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    if extension in {".mp4", ".mov", ".m4a"}:
        return len(header) >= 12 and header[4:8] == b"ftyp"
    if extension == ".webm":
        return header.startswith(b"\x1a\x45\xdf\xa3")
    if extension == ".mp3":
        return header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0)
    if extension in {".doc", ".xls"}:
        return header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if extension in {".zip", ".docx", ".xlsx"}:
        try:
            file_object.seek(0)
            with zipfile.ZipFile(file_object) as archive:
                names = set(archive.namelist())
            if extension == ".docx":
                return "[Content_Types].xml" in names and any(name.startswith("word/") for name in names)
            if extension == ".xlsx":
                return "[Content_Types].xml" in names and any(name.startswith("xl/") for name in names)
            return True
        except (OSError, zipfile.BadZipFile):
            return False
        finally:
            file_object.seek(0)
    if extension in {".txt", ".csv", ".html", ".htm"}:
        return _text_header_ok(header, truncated)
    return False
