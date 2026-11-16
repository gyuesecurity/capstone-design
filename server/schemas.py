"""요청 본문(JSON)의 모양. 기존 server/main.py의 Pydantic 모델을 그대로 옮겨 왔습니다.

주의: 모델 안에 docstring을 쓰면 API 문서(/openapi.json)에 설명이 추가되어 응답이 달라집니다.
2단계는 동작 변경이 없어야 하므로 설명은 주석(#)으로만 적습니다.
"""

from __future__ import annotations

from pydantic import BaseModel


# 로그인: 이메일과 비밀번호
class Credentials(BaseModel):
    email: str
    password: str


# 회원가입: 로그인 정보 + 이름
class Registration(Credentials):
    name: str


# 비밀번호 변경: 현재 비밀번호와 새 비밀번호
class PasswordChange(BaseModel):
    current_password: str
    new_password: str


# 프로필 수정: 이름
class ProfileUpdate(BaseModel):
    name: str


# 공유 추가·수정 (5단계, A 화면 app.js showShares가 보내는 모양 그대로)
# 보기(VIEW)는 항상 포함되므로 칸이 없습니다. download·verify는 체크박스 값입니다.
class ShareUpdate(BaseModel):
    email: str
    download: bool = False
    verify: bool = False


# 보고서 만들기 (7단계, A 화면 report-server.js generate()가 보내는 모양 그대로)
# author는 받지 않습니다. 보내도 무시하고 로그인한 사람 이름을 씁니다. (A API 명세 4장)
class ReportCreate(BaseModel):
    title: str
    project: str = ""
    description: str = ""
    selected: list[str]
    options: list[str]


# 패키지 만들기 (8단계, A 화면 report-packages.js가 보내는 모양 그대로)
class PackageCreate(BaseModel):
    reportId: str
