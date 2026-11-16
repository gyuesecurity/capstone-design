"""ReportRenderBundle v1 형식 검증 (docs/specs/report_render_bundle_v1.md).

형식만 확인한다. 해시 재계산·검증 판정·시각 파싱은 하지 않으며 묶음을 수정하지 않는다.
"""

import re

from d_package.errors import ReportBundleError

REPORT_ID = re.compile(r'RPT-[0-9a-f]{32}')
SHA256 = re.compile(r'[0-9a-f]{64}')
OPTIONS = {'basic', 'metadata', 'hash', 'results', 'custody'}
STATUSES = {'미검증', '일치', '불일치', '검증 불가'}

# 명세상 null인 칸도 키는 항상 존재하므로 모든 키를 필수로 본다
TOP_KEYS = ('bundleVersion', 'reportId', 'created', 'timeZone', 'report', 'evidence')
REPORT_KEYS = ('title', 'project', 'description', 'author', 'options')
EVIDENCE_KEYS = (
    'id', 'name', 'filename', 'size', 'registeredAt', 'registeredBy', 'source',
    'mime', 'sha256', 'status', 'verification', 'custodySnapshot',
)
VERIFICATION_KEYS = ('completedAt', 'performedBy', 'currentHash', 'reason')
SNAPSHOT_KEYS = ('sequenceNo', 'eventHash')


def _object(value, path, keys):
    if not isinstance(value, dict):
        raise ReportBundleError('FIELD_TYPE', path)
    # C 확인 필요: 알 수 없는 필드는 거부 (필드 이름도 입력값이라 경로에 넣지 않음)
    if any(key not in keys for key in value):
        raise ReportBundleError('FIELD_UNKNOWN', path)
    for key in keys:
        if key not in value:
            raise ReportBundleError('FIELD_MISSING', f'{path}.{key}')


def _string(value, path, max_len=None, pattern=None, choices=None, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str):
        raise ReportBundleError('FIELD_TYPE', path)
    if max_len is not None and len(value) > max_len:
        raise ReportBundleError('FIELD_LENGTH', path)
    if pattern is not None and not pattern.fullmatch(value):
        raise ReportBundleError('FIELD_FORMAT', path)
    if choices is not None and value not in choices:
        raise ReportBundleError('FIELD_FORMAT', path)


def _integer(value, path, minimum=None):
    # bool은 int의 하위 타입이라 명시적으로 제외
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReportBundleError('FIELD_TYPE', path)
    if minimum is not None and value < minimum:
        raise ReportBundleError('FIELD_FORMAT', path)


def _report(report):
    _object(report, 'report', REPORT_KEYS)
    _string(report['title'], 'report.title', max_len=150)
    _string(report['project'], 'report.project', max_len=200, nullable=True)
    _string(report['description'], 'report.description', max_len=4000, nullable=True)
    _string(report['author'], 'report.author', nullable=True)

    options = report['options']
    if not isinstance(options, list):
        raise ReportBundleError('FIELD_TYPE', 'report.options')
    seen = set()
    for i, option in enumerate(options):
        path = f'report.options[{i}]'
        _string(option, path, choices=OPTIONS)
        if option in seen:
            raise ReportBundleError('FIELD_FORMAT', path)
        seen.add(option)


def _evidence(item, path):
    _object(item, path, EVIDENCE_KEYS)
    # C 확인 필요: 증거 ID 형식(EV-...)은 검사하지 않음
    _string(item['id'], f'{path}.id')
    _string(item['name'], f'{path}.name')
    _string(item['filename'], f'{path}.filename')
    _integer(item['size'], f'{path}.size', minimum=0)
    # C 확인 필요: registeredAt·custodySnapshot은 명세 표대로 null 불가
    _string(item['registeredAt'], f'{path}.registeredAt')
    _string(item['registeredBy'], f'{path}.registeredBy', nullable=True)
    _string(item['source'], f'{path}.source', nullable=True)
    _string(item['mime'], f'{path}.mime', nullable=True)
    _string(item['sha256'], f'{path}.sha256', pattern=SHA256)
    _string(item['status'], f'{path}.status', choices=STATUSES)

    verification = item['verification']
    if verification is not None:
        vpath = f'{path}.verification'
        _object(verification, vpath, VERIFICATION_KEYS)
        _string(verification['completedAt'], f'{vpath}.completedAt')
        _string(verification['performedBy'], f'{vpath}.performedBy', nullable=True)
        _string(verification['currentHash'], f'{vpath}.currentHash', pattern=SHA256, nullable=True)
        _string(verification['reason'], f'{vpath}.reason', nullable=True)

    spath = f'{path}.custodySnapshot'
    snapshot = item['custodySnapshot']
    _object(snapshot, spath, SNAPSHOT_KEYS)
    _integer(snapshot['sequenceNo'], f'{spath}.sequenceNo')
    _string(snapshot['eventHash'], f'{spath}.eventHash', pattern=SHA256)


def validate_report_bundle(bundle):
    """형식이 틀리면 ReportBundleError(code, path)를 낸다. 통과 시 None."""
    _object(bundle, 'bundle', TOP_KEYS)
    _string(bundle['bundleVersion'], 'bundleVersion', choices={'report-v1'})
    _string(bundle['reportId'], 'reportId', pattern=REPORT_ID)
    _string(bundle['created'], 'created')
    _string(bundle['timeZone'], 'timeZone')
    _report(bundle['report'])

    evidence = bundle['evidence']
    # C 확인 필요: 빈 evidence 배열 허용
    if not isinstance(evidence, list):
        raise ReportBundleError('FIELD_TYPE', 'evidence')
    for i, item in enumerate(evidence):
        _evidence(item, f'evidence[{i}]')
