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
