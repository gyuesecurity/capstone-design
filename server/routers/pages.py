"""화면 파일 제공 (11/22 추가). start-server.cmd 하나로 화면과 API를 같은 주소(127.0.0.1:8000)에서 쓰기 위해서입니다.

- 화면은 두 버전입니다. 둘 다 같은 계정·증거 데이터를 씁니다. (프론트가 만든 server/web_files.py의 주소 목록을 그대로 사용)
    기존 버전  versions/previous/<기능>/  →  /index.html, /evidence.html, /assets/report/… (예전 주소 그대로)
    Page 4    versions/page4/<기능>/     →  /page4/index.html, /page4/evidence.html, …   (11/23 추가)
- 목록에 있는 파일만 보냅니다. 주소로 파일 경로를 만들지 않으므로 ../ 같은 경로로 다른 파일을 열 수 없습니다.
- /api/… 와 /docs 등 다른 주소보다 "마지막"에 연결해야 합니다. (main.py에서 맨 끝에 include)
- 서버가 켜질 때 목록을 한 번 만듭니다. 화면 파일 내용을 고치면 바로 반영되고, 새 파일을 추가하면 서버를 다시 켜야 합니다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from ..web_files import public_files

router = APIRouter()
_FILES = public_files("previous")
_FILES.update({f"page4/{url}": source for url, source in public_files("page4").items()})


@router.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/index.html")


@router.get("/page4", include_in_schema=False)
@router.get("/page4/", include_in_schema=False)
def page4_home():
    return RedirectResponse("/page4/index.html")


@router.get("/{file_path:path}", include_in_schema=False)
def page_file(file_path: str):
    # /api/… 는 여기까지 오면 없는 API 주소라서 화면 대신 JSON 404를 돌려줍니다.
    if file_path == "api" or file_path.startswith("api/"):
        raise HTTPException(404, "Not Found")
    target = _FILES.get(file_path)
    if target is None or not target.is_file():
        raise HTTPException(404, "Not Found")
    return FileResponse(target)
