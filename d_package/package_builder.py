"""PackageBuildBundle v1 → ZIP (docs/specs/package_build_bundle_v1.md).

entries의 path·data를 순서대로 그대로 쓴다. 내용(JSON·PDF)은 열거나 고치지 않는다.
출력(print/로그)과 결과 캐시 없음. output 정리·삭제는 C 담당.
"""

import hashlib
import re
import zipfile
from typing import BinaryIO

from d_package.errors import PackageBuildError, PackageBundleError

__all__ = ['PackageBuildError', 'PackageBundleError', 'build_package_zip']

PACKAGE_ID = re.compile(r'PKG-[0-9a-f]{32}')
REPORT_ID = re.compile(r'RPT-[0-9a-f]{32}')
SHA256 = re.compile(r'[0-9a-f]{64}')
DRIVE = re.compile(r'[A-Za-z]:')
CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f]')
KINDS = {'manifest', 'verification', 'custody', 'report', 'evidence'}

TOP_KEYS = ('bundleVersion', 'packageId', 'reportId', 'created', 'creator', 'entries')
ENTRY_KEYS = ('path', 'kind', 'data', 'size', 'sha256', 'evidenceId')

# 명세에 없어 고정값 사용(B안): 현재 시각·created 해석 없이 같은 묶음 → 같은 ZIP 바이트
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def _object(value, path, keys):
    if not isinstance(value, dict):
        raise PackageBundleError('FIELD_TYPE', path)
    # 알 수 없는 필드는 거부 (필드 이름도 입력값이라 위치에 넣지 않음)
    if any(key not in keys for key in value):
        raise PackageBundleError('FIELD_UNKNOWN', path)
    for key in keys:
        if key not in value:
            raise PackageBundleError('FIELD_MISSING', f'{path}.{key}')


def _check_path(value, where, package_id):
    if not isinstance(value, str):
        raise PackageBundleError('FIELD_TYPE', where)
    if (
        not value.startswith(package_id + '/')
        or value.startswith(('/', '\\'))
        or DRIVE.match(value)
        or '\\' in value
        or value.endswith('/')
        or CONTROL.search(value)
        # 빈 세그먼트(//)·'.'·'..' 거부. '.'은 지시 목록 밖이지만 '..'와 같은 이유로 거부
        or any(seg in ('', '.', '..') for seg in value.split('/'))
    ):
        raise PackageBundleError('FIELD_FORMAT', where)


def _check_entry(entry, where, package_id):
    _object(entry, where, ENTRY_KEYS)
    _check_path(entry['path'], f'{where}.path', package_id)

    kind = entry['kind']
    if not isinstance(kind, str):
        raise PackageBundleError('FIELD_TYPE', f'{where}.kind')
    if kind not in KINDS:
        raise PackageBundleError('FIELD_FORMAT', f'{where}.kind')

    # bytes만 허용: bytearray·memoryview는 검사 후 쓰기 전에 내용이 바뀔 수 있음
    data = entry['data']
    if type(data) is not bytes:
        raise PackageBundleError('FIELD_TYPE', f'{where}.data')

    size = entry['size']
    if isinstance(size, bool) or not isinstance(size, int):
        raise PackageBundleError('FIELD_TYPE', f'{where}.size')
    if size != len(data):
        raise PackageBundleError('DATA_MISMATCH', f'{where}.size')

    digest = entry['sha256']
    if not isinstance(digest, str):
        raise PackageBundleError('FIELD_TYPE', f'{where}.sha256')
    if not SHA256.fullmatch(digest):
        raise PackageBundleError('FIELD_FORMAT', f'{where}.sha256')
    # 명세 요구 대조: 계산값은 비교에만 쓰고 저장하지 않음
    if hashlib.sha256(data).hexdigest() != digest:
        raise PackageBundleError('DATA_MISMATCH', f'{where}.sha256')

    evidence_id = entry['evidenceId']
    # C 확인 필요: 증거 ID 형식은 검사하지 않음
    if kind == 'evidence' and not isinstance(evidence_id, str):
        raise PackageBundleError('FIELD_TYPE', f'{where}.evidenceId')
    if kind != 'evidence' and evidence_id is not None:
        raise PackageBundleError('FIELD_TYPE', f'{where}.evidenceId')


def validate_package_bundle(bundle):
    """형식이 틀리면 PackageBundleError(code, path)를 낸다. 통과 시 None."""
    _object(bundle, 'bundle', TOP_KEYS)
    for key, pattern in (('bundleVersion', None), ('packageId', PACKAGE_ID), ('reportId', REPORT_ID)):
        if not isinstance(bundle[key], str):
            raise PackageBundleError('FIELD_TYPE', key)
        if pattern is not None and not pattern.fullmatch(bundle[key]):
            raise PackageBundleError('FIELD_FORMAT', key)
    if bundle['bundleVersion'] != 'package-v1':
        raise PackageBundleError('FIELD_FORMAT', 'bundleVersion')
    for key in ('created', 'creator'):
        if not isinstance(bundle[key], str):
            raise PackageBundleError('FIELD_TYPE', key)

    entries = bundle['entries']
    if not isinstance(entries, list):
        raise PackageBundleError('FIELD_TYPE', 'entries')
    if not entries:
        raise PackageBundleError('FIELD_FORMAT', 'entries')

    # C 확인 필요: kind 순서·개수와 대소문자/NFC만 다른 경로 충돌은 검사하지 않음 (명세: 정확히 같은 경로만)
    seen = set()
    for i, entry in enumerate(entries):
        where = f'entries[{i}]'
        _check_entry(entry, where, bundle['packageId'])
        if entry['path'] in seen:
            raise PackageBundleError('PATH_DUPLICATE', f'{where}.path')
        seen.add(entry['path'])


def build_package_zip(bundle: dict, output: BinaryIO) -> None:
    """PackageBuildBundle v1을 받아 ZIP을 output에 쓴다. 디스크의 다른 파일은 읽거나 쓰지 않는다.

    형식 오류는 PackageBundleError(output에 아무것도 쓰지 않음), 작성 중 실패는 PackageBuildError.
    """
    validate_package_bundle(bundle)

    try:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for entry in bundle['entries']:
                info = zipfile.ZipInfo(entry['path'], date_time=FIXED_DATE_TIME)
                # ZipInfo로 쓰면 ZipFile의 compression이 적용되지 않아 직접 지정
                info.compress_type = zipfile.ZIP_DEFLATED
                # 실행 OS와 무관하게 같은 결과, Linux 해제 시 권한 0644
                info.create_system = 3
                info.external_attr = 0o644 << 16
                zf.writestr(info, entry['data'])
    # 예외 메시지에 경로가 섞일 수 있어 고정 메시지로 바꾸고 원인 연결을 끊는다
    except Exception:  # noqa: BLE001
        raise PackageBuildError('ZIP 작성 실패') from None
