import copy
import json
import unittest
from pathlib import Path

from d_package.errors import ReportBundleError
from d_package.report_bundle import validate_report_bundle

FIXTURES = Path(__file__).parent / 'fixtures'
SECRET = 'SECRET-7f3a<script>'


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def setter(path, value):
    """'evidence.0.sha256' 같은 경로의 값을 바꾸는 함수를 만든다."""
    *parents, last = path.split('.')

    def mutate(bundle):
        target = bundle
        for key in parents:
            target = target[int(key)] if isinstance(target, list) else target[key]
        target[int(last) if isinstance(target, list) else last] = value

    return mutate


def remover(path):
    *parents, last = path.split('.')

    def mutate(bundle):
        target = bundle
        for key in parents:
            target = target[int(key)] if isinstance(target, list) else target[key]
        del target[last]

    return mutate


CASES = [
    # (설명, 변형, 기대 코드, 기대 경로)
    ('묶음이 dict 아님', None, 'FIELD_TYPE', 'bundle'),
    ('최상위 필드 누락', remover('timeZone'), 'FIELD_MISSING', 'bundle.timeZone'),
    ('최상위 알 수 없는 필드', setter('jobId', 'JOB-1'), 'FIELD_UNKNOWN', 'bundle'),
    ('버전 불일치', setter('bundleVersion', 'v1.0'), 'FIELD_FORMAT', 'bundleVersion'),
    ('reportId 대문자', setter('reportId', 'RPT-' + 'A' * 32), 'FIELD_FORMAT', 'reportId'),
    ('reportId 짧음', setter('reportId', 'RPT-abc'), 'FIELD_FORMAT', 'reportId'),
    ('reportId 끝 줄바꿈', setter('reportId', 'RPT-' + 'a' * 32 + '\n'), 'FIELD_FORMAT', 'reportId'),
    ('created 숫자', setter('created', 20261111), 'FIELD_TYPE', 'created'),
    ('timeZone null', setter('timeZone', None), 'FIELD_TYPE', 'timeZone'),
    ('report 배열', setter('report', []), 'FIELD_TYPE', 'report'),
    ('title null', setter('report.title', None), 'FIELD_TYPE', 'report.title'),
    ('title 151자', setter('report.title', '가' * 151), 'FIELD_LENGTH', 'report.title'),
    ('project 201자', setter('report.project', 'a' * 201), 'FIELD_LENGTH', 'report.project'),
    ('description 4001자', setter('report.description', 'a' * 4001), 'FIELD_LENGTH', 'report.description'),
    ('author 숫자', setter('report.author', 1), 'FIELD_TYPE', 'report.author'),
    ('options 문자열', setter('report.options', 'basic'), 'FIELD_TYPE', 'report.options'),
    ('options 알 수 없는 값', setter('report.options', ['basic', 'extra']), 'FIELD_FORMAT', 'report.options[1]'),
    ('options 중복', setter('report.options', ['hash', 'hash']), 'FIELD_FORMAT', 'report.options[1]'),
    ('options 항목 숫자', setter('report.options', [1]), 'FIELD_TYPE', 'report.options[0]'),
    ('evidence dict', setter('evidence', {}), 'FIELD_TYPE', 'evidence'),
    ('evidence 항목 문자열', setter('evidence', ['x']), 'FIELD_TYPE', 'evidence[0]'),
    ('evidence 필드 누락', remover('evidence.0.filename'), 'FIELD_MISSING', 'evidence[0].filename'),
    ('evidence 알 수 없는 필드', setter('evidence.0.evidenceId', 'EVI-001'), 'FIELD_UNKNOWN', 'evidence[0]'),
    ('id null', setter('evidence.0.id', None), 'FIELD_TYPE', 'evidence[0].id'),
    ('name null', setter('evidence.0.name', None), 'FIELD_TYPE', 'evidence[0].name'),
    ('size bool', setter('evidence.0.size', True), 'FIELD_TYPE', 'evidence[0].size'),
    ('size 실수', setter('evidence.0.size', 1.5), 'FIELD_TYPE', 'evidence[0].size'),
    ('size 음수', setter('evidence.0.size', -1), 'FIELD_FORMAT', 'evidence[0].size'),
    ('registeredAt null', setter('evidence.0.registeredAt', None), 'FIELD_TYPE', 'evidence[0].registeredAt'),
    ('mime 숫자', setter('evidence.0.mime', 0), 'FIELD_TYPE', 'evidence[0].mime'),
    ('sha256 대문자', setter('evidence.0.sha256', 'A' * 64), 'FIELD_FORMAT', 'evidence[0].sha256'),
    ('sha256 63자', setter('evidence.0.sha256', 'a' * 63), 'FIELD_FORMAT', 'evidence[0].sha256'),
    ('status 알 수 없는 값', setter('evidence.0.status', '통과'), 'FIELD_FORMAT', 'evidence[0].status'),
    ('verification 문자열', setter('evidence.0.verification', 'ok'), 'FIELD_TYPE', 'evidence[0].verification'),
    ('verification 필드 누락', remover('evidence.0.verification.reason'), 'FIELD_MISSING',
     'evidence[0].verification.reason'),
    ('completedAt null', setter('evidence.0.verification.completedAt', None), 'FIELD_TYPE',
     'evidence[0].verification.completedAt'),
    ('currentHash 형식', setter('evidence.0.verification.currentHash', 'abc'), 'FIELD_FORMAT',
     'evidence[0].verification.currentHash'),
    ('custodySnapshot null', setter('evidence.0.custodySnapshot', None), 'FIELD_TYPE',
     'evidence[0].custodySnapshot'),
    ('custodySnapshot 옛 문자열', setter('evidence.0.custodySnapshot', 'SNAP-1122'), 'FIELD_TYPE',
     'evidence[0].custodySnapshot'),
    ('sequenceNo 문자열', setter('evidence.0.custodySnapshot.sequenceNo', '3'), 'FIELD_TYPE',
     'evidence[0].custodySnapshot.sequenceNo'),
    ('sequenceNo bool', setter('evidence.0.custodySnapshot.sequenceNo', False), 'FIELD_TYPE',
     'evidence[0].custodySnapshot.sequenceNo'),
    ('eventHash 형식', setter('evidence.0.custodySnapshot.eventHash', 'z' * 64), 'FIELD_FORMAT',
     'evidence[0].custodySnapshot.eventHash'),
]


class ReportBundleValidationTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load('mock_report_bundle.json')

    def test_fixtures_pass(self):
        for name in ('mock_report_bundle.json', 'mock_report_bundle_nulls.json'):
            with self.subTest(fixture=name):
                bundle = load(name)
                before = copy.deepcopy(bundle)
                self.assertIsNone(validate_report_bundle(bundle))
                self.assertEqual(bundle, before)  # 묶음을 수정하지 않음

    def test_empty_evidence_allowed(self):
        self.bundle['evidence'] = []
        validate_report_bundle(self.bundle)

    def test_empty_options_allowed(self):
        self.bundle['report']['options'] = []
        validate_report_bundle(self.bundle)

    def test_length_limits_inclusive(self):
        self.bundle['report']['title'] = '가' * 150
        self.bundle['report']['project'] = 'a' * 200
        self.bundle['report']['description'] = 'a' * 4000
        validate_report_bundle(self.bundle)

    def test_time_strings_not_parsed(self):
        # 시각 문자열은 형식 검사·재포맷 대상 아님
        self.bundle['created'] = 'not-a-date'
        validate_report_bundle(self.bundle)
        self.assertEqual(self.bundle['created'], 'not-a-date')

    def test_rule_failures(self):
        for desc, mutate, code, path in CASES:
            with self.subTest(desc):
                bundle = copy.deepcopy(self.bundle)
                if mutate is None:
                    bundle = [bundle]
                else:
                    mutate(bundle)
                with self.assertRaises(ReportBundleError) as ctx:
                    validate_report_bundle(bundle)
                self.assertEqual((ctx.exception.code, ctx.exception.path), (code, path))
                self.assertIsInstance(ctx.exception, ValueError)

    def test_error_excludes_input_values(self):
        leaks = [
            setter('reportId', SECRET),
            setter('report.title', SECRET * 20),
            setter('report.options', [SECRET]),
            setter('evidence.0.status', SECRET),
            setter('evidence.0.verification.currentHash', SECRET),
            setter(SECRET, 'x'),  # 알 수 없는 필드 이름
            setter(f'evidence.0.{SECRET}', 'x'),
        ]
        for i, mutate in enumerate(leaks):
            with self.subTest(case=i):
                bundle = copy.deepcopy(self.bundle)
                mutate(bundle)
                with self.assertRaises(ReportBundleError) as ctx:
                    validate_report_bundle(bundle)
                err = ctx.exception
                for text in (str(err), repr(err), *map(str, err.args)):
                    self.assertNotIn('SECRET', text)
                self.assertIsNone(err.__cause__)
                self.assertIsNone(err.__context__)


if __name__ == '__main__':
    unittest.main()
