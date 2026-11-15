import copy
import hashlib
import io
import unittest
import zipfile
from unittest import mock

from d_package.package_builder import (
    FIXED_DATE_TIME,
    PackageBuildError,
    PackageBundleError,
    build_package_zip,
)

PKG = 'PKG-3f2a9c1e5b7d4e8a9c217d4e5f6a8b90'
EV1 = 'EV-0f3c2d1e9a8b4c7d8e6f5a4b3c2d1e0f'
EV2 = 'EV-9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d'
# 공백·키 순서가 특이한 JSON: 파싱 후 재직렬화하면 바뀌는 바이트
ODD_JSON = b'{ "z" : 1,\n\t"a":[ 2 ,3 ],"\xed\x95\x9c":  null }\r\n'


def entry(path, kind, data, evidence_id=None):
    return {
        'path': f'{PKG}/{path}', 'kind': kind, 'data': data, 'size': len(data),
        'sha256': hashlib.sha256(data).hexdigest(), 'evidenceId': evidence_id,
    }


def make_bundle():
    return {
        'bundleVersion': 'package-v1',
        'packageId': PKG,
        'reportId': 'RPT-1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d',
        'created': '2026/11/12 01:10:00',
        'creator': '홍길동',
        'entries': [
            entry('manifest.json', 'manifest', ODD_JSON),
            entry('verification.json', 'verification', b'[]'),
            entry('custody.json', 'custody', b'[{"seq":1}]'),
            entry('report/report.pdf', 'report', b'%PDF-1.7 fake'),
            entry(f'evidence/01_{EV1}_chat_capture_01.png', 'evidence', b'\x89PNG fake', EV1),
            entry(f'evidence/02_{EV2}_협박_메시지.pdf', 'evidence', b'%PDF-1.4 fake2', EV2),
        ],
    }


def build(bundle):
    out = io.BytesIO()
    build_package_zip(bundle, out)
    return out.getvalue()


class UnseekableStream(io.RawIOBase):
    """write만 되는 출력 (C가 파이프 등으로 넘기는 경우)."""

    def __init__(self):
        self.buffer = bytearray()

    def writable(self):
        return True

    def seekable(self):
        return False

    def tell(self):
        raise OSError('unseekable')

    def write(self, b):
        self.buffer += b
        return len(b)


class PackageBuilderTests(unittest.TestCase):
    def setUp(self):
        self.bundle = make_bundle()

    def assert_matches_bundle(self, zip_bytes, bundle):
        """C의 결과 검사 기준 그대로."""
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            self.assertIsNone(zf.testzip())
            self.assertEqual(zf.namelist(), [e['path'] for e in bundle['entries']])
            for info, e in zip(zf.infolist(), bundle['entries'], strict=True):
                self.assertFalse(info.flag_bits & 0x1)  # 암호화 없음
                data = zf.read(info)
                self.assertEqual(len(data), e['size'])
                self.assertEqual(hashlib.sha256(data).hexdigest(), e['sha256'])

    def test_zip_matches_entries(self):
        self.assert_matches_bundle(build(self.bundle), self.bundle)

    def test_no_directory_entries_and_metadata(self):
        with zipfile.ZipFile(io.BytesIO(build(self.bundle))) as zf:
            for info in zf.infolist():
                self.assertFalse(info.is_dir())
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                self.assertEqual(info.date_time, FIXED_DATE_TIME)

    def test_json_bytes_preserved(self):
        with zipfile.ZipFile(io.BytesIO(build(self.bundle))) as zf:
            self.assertEqual(zf.read(f'{PKG}/manifest.json'), ODD_JSON)

    def test_korean_name_utf8_flag(self):
        with zipfile.ZipFile(io.BytesIO(build(self.bundle))) as zf:
            korean = [i for i in zf.infolist() if '협박' in i.filename]
            self.assertEqual(len(korean), 1)
            self.assertTrue(korean[0].flag_bits & 0x800)

    def test_same_bundle_twice(self):
        first, second = build(self.bundle), build(self.bundle)
        self.assert_matches_bundle(first, self.bundle)
        self.assert_matches_bundle(second, self.bundle)
        self.assertEqual(first, second)  # 고정 date_time → 재현 가능

    def test_unseekable_output(self):
        out = UnseekableStream()
        build_package_zip(self.bundle, out)
        self.assert_matches_bundle(bytes(out.buffer), self.bundle)

    def test_write_failure_raises_build_error(self):
        with mock.patch('zipfile.ZipFile.writestr', side_effect=OSError(f'{PKG}/secret.png')), \
                self.assertRaises(PackageBuildError) as ctx:
            build(self.bundle)
        self.assertNotIn(PKG, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)


def set_entry(index, key, value):
    def mutate(bundle):
        bundle['entries'][index][key] = value
    return mutate


def set_path(index, suffix):
    return set_entry(index, 'path', f'{PKG}/{suffix}')


def set_top(key, value):
    def mutate(bundle):
        bundle[key] = value
    return mutate


INVALID = [
    # (설명, 변형, 기대 코드, 기대 위치)
    ('묶음 list', None, 'FIELD_TYPE', 'bundle'),
    ('최상위 알 수 없는 필드', set_top('extra', 1), 'FIELD_UNKNOWN', 'bundle'),
    ('버전 불일치', set_top('bundleVersion', 'package-v2'), 'FIELD_FORMAT', 'bundleVersion'),
    ('packageId 형식', set_top('packageId', 'PKG-ABC'), 'FIELD_FORMAT', 'packageId'),
    ('reportId 형식', set_top('reportId', 'RPT-' + 'A' * 32), 'FIELD_FORMAT', 'reportId'),
    ('creator null', set_top('creator', None), 'FIELD_TYPE', 'creator'),
    ('created 숫자', set_top('created', 1), 'FIELD_TYPE', 'created'),
    ('entries 빈 배열', set_top('entries', []), 'FIELD_FORMAT', 'entries'),
    ('entries dict', set_top('entries', {}), 'FIELD_TYPE', 'entries'),
    ('entry 알 수 없는 필드', set_entry(0, 'extra', 1), 'FIELD_UNKNOWN', 'entries[0]'),
    ('path 숫자', set_entry(0, 'path', 1), 'FIELD_TYPE', 'entries[0].path'),
    ('path 다른 패키지', set_entry(0, 'path', 'PKG-' + '0' * 32 + '/manifest.json'), 'FIELD_FORMAT',
     'entries[0].path'),
    ('path 접두어만', set_entry(0, 'path', PKG), 'FIELD_FORMAT', 'entries[0].path'),
    ('path 절대경로', set_entry(0, 'path', f'/{PKG}/manifest.json'), 'FIELD_FORMAT', 'entries[0].path'),
    ('path 드라이브', set_entry(0, 'path', f'C:{PKG}/manifest.json'), 'FIELD_FORMAT', 'entries[0].path'),
    ('path 역슬래시', set_path(0, 'report\\report.pdf'), 'FIELD_FORMAT', 'entries[0].path'),
    ('path ..', set_path(0, '../escape.json'), 'FIELD_FORMAT', 'entries[0].path'),
    ('path .', set_path(0, './manifest.json'), 'FIELD_FORMAT', 'entries[0].path'),
    ('path 빈 세그먼트', set_path(0, 'report//report.pdf'), 'FIELD_FORMAT', 'entries[0].path'),
    ('path / 끝', set_path(0, 'report/'), 'FIELD_FORMAT', 'entries[0].path'),
    ('path 줄바꿈', set_path(0, 'mani\nfest.json'), 'FIELD_FORMAT', 'entries[0].path'),
    ('path NUL', set_path(0, 'mani\x00fest.json'), 'FIELD_FORMAT', 'entries[0].path'),
    ('path C1 제어문자', set_path(0, 'mani\x85fest.json'), 'FIELD_FORMAT', 'entries[0].path'),
    ('path 중복', set_path(5, 'verification.json'), 'PATH_DUPLICATE', 'entries[5].path'),
    ('kind 오류', set_entry(0, 'kind', 'readme'), 'FIELD_FORMAT', 'entries[0].kind'),
    ('kind 숫자', set_entry(0, 'kind', 1), 'FIELD_TYPE', 'entries[0].kind'),
    ('data str', set_entry(1, 'data', '[]'), 'FIELD_TYPE', 'entries[1].data'),
    ('data bytearray', set_entry(1, 'data', bytearray(b'[]')), 'FIELD_TYPE', 'entries[1].data'),
    ('data memoryview', set_entry(1, 'data', memoryview(b'[]')), 'FIELD_TYPE', 'entries[1].data'),
    ('size 불일치', set_entry(1, 'size', 3), 'DATA_MISMATCH', 'entries[1].size'),
    ('size bool', set_entry(1, 'size', True), 'FIELD_TYPE', 'entries[1].size'),
    ('sha256 형식', set_entry(1, 'sha256', 'A' * 64), 'FIELD_FORMAT', 'entries[1].sha256'),
    ('sha256 불일치', set_entry(1, 'sha256', '0' * 64), 'DATA_MISMATCH', 'entries[1].sha256'),
    ('evidenceId 누락(evidence)', set_entry(4, 'evidenceId', None), 'FIELD_TYPE', 'entries[4].evidenceId'),
    ('evidenceId 있음(report)', set_entry(3, 'evidenceId', EV1), 'FIELD_TYPE', 'entries[3].evidenceId'),
]


class PackageBundleValidationTests(unittest.TestCase):
    def test_invalid_bundles_write_nothing(self):
        for desc, mutate, code, where in INVALID:
            with self.subTest(desc):
                bundle = make_bundle()
                if mutate is None:
                    bundle = [bundle]
                else:
                    mutate(bundle)
                out = io.BytesIO()
                with self.assertRaises(PackageBundleError) as ctx:
                    build_package_zip(bundle, out)
                self.assertEqual((ctx.exception.code, ctx.exception.path), (code, where))
                self.assertEqual(out.getvalue(), b'')
                # 메시지에 ZIP 안 경로·파일명이 없음
                self.assertNotIn(PKG, str(ctx.exception))
                self.assertNotIn('manifest', str(ctx.exception))

    def test_bundle_not_mutated(self):
        bundle = make_bundle()
        before = copy.deepcopy(bundle)
        build(bundle)
        self.assertEqual(bundle, before)


if __name__ == '__main__':
    unittest.main()
