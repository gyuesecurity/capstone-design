# 해시·암호화 모듈 인터페이스 v1

이 문서는 백엔드(C)가 암호화 모듈(B)을 호출하는 규격을 고정합니다. B는 전달받은 바이트만 처리하며 DB, 영구 저장 위치, 사용자 권한, 상태 판정과 Chain of Custody 기록은 C가 담당합니다.

## 확정 사항

| 항목 | v1 결정 |
| --- | --- |
| 실행 형태 | 서버 내부 Python 모듈 |
| 해시 | SHA-256, 소문자 16진수 64자리 |
| 해시 비교 | B는 계산값만 반환하고 C가 기준 해시와 비교 |
| 암호화 | AES-256-GCM |
| 마스터 키 | 32바이트 버전별 키링, B가 Docker secret 또는 저장소 밖의 키링 파일에서 읽음 |
| 파일별 키 | 16바이트 무작위 salt와 HKDF-SHA256으로 파생 |
| nonce / 인증 태그 | 매 호출마다 새로운 12바이트 nonce / 16바이트 태그 |
| 저장 형식 | 저장소에는 암호문만 저장; salt, nonce, tag, key ID, AAD 버전은 DB에 별도 저장 |
| 저장 크기 | 태그를 분리하므로 `stored_size == plaintext_size` |
| 입력 경계 | C가 바이트 조각을 공급하고 B는 경로나 영구 저장 위치를 알지 않음 |
| 최대 증거 크기 | API 정책상 50 MiB; 암호화와 해시는 1 MiB 조각으로 처리 |

보고서 PDF와 패키지 ZIP의 암호화 적용 여부는 팀 결정 전이므로 이 규격의 호출 대상에는 포함하되 API 연동은 보류합니다.

## 호출 인터페이스

### SHA-256

```python
from server.crypto import Sha256Hasher, sha256_chunks

digest = Sha256Hasher()
digest.update(first_chunk)
digest.update(second_chunk)
sha256_hex = digest.hexdigest()

# 동기 iterable을 사용할 수 있으면 동일한 축약형
sha256_hex = sha256_chunks(chunks)
```

입력 조각에는 변환하지 않은 원본 바이트를 전달합니다. 문자열이나 다른 자료형은 `InvalidCryptoInputError`입니다.

### 암호화

```python
session = evidence_crypto.start_encryption(aad)
for plain_chunk in chunks:
    encrypted_file.write(session.update(plain_chunk))
final_chunk, result = session.finalize(aad_version="v1")
encrypted_file.write(final_chunk)

metadata = result.metadata
stored_size = result.stored_size
```

`EncryptionResult.metadata`에는 `algorithm`, `key_version`, `salt`, `nonce`, `tag`, `aad_version`이 들어 있습니다. 키 자체는 반환하지 않습니다. 한 세션을 두 파일에 재사용하거나 `finalize()` 이후 다시 입력하면 안 됩니다.

### 복호화

```python
plain = evidence_crypto.decrypt_verified(encrypted_chunks, metadata, aad)
try:
    use_verified_plaintext(plain)
finally:
    plain.close()
```

반환값은 seek 가능한 임시 스트림입니다. B가 전체 암호문의 인증 태그를 확인한 후에만 반환하므로, 인증 실패 시 C는 일부 평문도 받지 않습니다. C는 반환된 스트림을 반드시 닫아야 합니다.

## 실패 구분

| 예외 | 의미 | C 처리 원칙 |
| --- | --- | --- |
| `AuthenticationFailedError` | 암호문, nonce, 태그 또는 AAD 불일치 | 무결성 검증에서는 `MISMATCH / AUTHENTICATION_FAILED` 기록 |
| `KeyUnavailableError` | key ID에 해당하는 키가 없음 | HTTP 500, 변조 결과로 기록하지 않음 |
| `KeyConfigurationError` | 서버 키 소스·키링·활성 키 설정 오류 | 서버 시작 중단 |
| `InvalidEncryptionMetadataError` | 알고리즘·key ID·salt·nonce·tag·AAD 버전 형식 오류 | HTTP 500, 검증 결과를 저장하지 않음 |
| `InvalidCryptoInputError` | 모듈 호출 바이트·세션 상태 오류 | HTTP 500, 개발 점검 필요 |
| `CryptoInternalError` | 예상하지 못한 암호 라이브러리 오류 | HTTP 500, 민감정보 없는 서버 로그만 허용 |

파일 없음과 파일 읽기 실패는 C의 저장소 오류이며 위 네 예외에 포함하지 않습니다.

## DB 매핑

| 모듈 값 | DB 컬럼 |
| --- | --- |
| `metadata.algorithm` | `encryption_algorithm` |
| `metadata.key_version` | `encryption_key_version` |
| `metadata.salt` | `encryption_salt` |
| `metadata.nonce` | `encryption_nonce` |
| `metadata.tag` | `encryption_tag` |
| `metadata.aad_version` | `encryption_aad_version` |
| `result.stored_size` | `stored_size` |

증거의 기준 `sha256`은 암호화 전 원본의 값이며 재암호화 후에도 바꾸지 않습니다.

## 연동 테스트 벡터

자동화된 기준은 `tests/test_crypto.py`에 있습니다.

- 빈 입력 SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- `abc` SHA-256: `ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad`
- 같은 평문을 두 번 암호화하면 salt, nonce와 암호문이 달라야 함
- 정상 암호문은 원문으로 복원되어야 함
- 암호문 1바이트 변경 시 `AuthenticationFailedError`여야 함
- 없는 key ID는 `KeyUnavailableError`여야 하며 검증 결과를 변조로 저장하면 안 됨

실제 운영 키, 평문, 키 파생값은 로그·오류 메시지·테스트 자료에 남기지 않습니다.

## 키 주입과 개발 모드

- 기본 동작은 fail-closed이며 키가 없으면 서버가 시작되지 않습니다.
- 운영·시연은 `EVIDENCE_MASTER_KEYS_FILE`로 데이터 볼륨 밖의 JSON 키링을 읽습니다.
- `EVIDENCE_MASTER_KEYS` 환경변수와 단일 `EVIDENCE_MASTER_KEY`는 호환용이며 키 소스를 동시에 두 개 이상 설정할 수 없습니다.
- 로컬 자동 생성은 `EVIDENCE_ALLOW_DEV_KEY_GENERATION=true`를 명시했을 때만 허용합니다.
- MVP는 키링 형식을 유지하되 `v1` 한 개만 운용합니다.

## AAD v1 동결

AAD v1에는 `schema: "v1"`, `evidenceId`, `ownerId`, `name`, `filename`, `kind`, `source`, `registeredAt`, `keyVersion`만 포함합니다. 표시용 등록자 이름은 포함하지 않습니다. 기존 파일을 계속 복호화할 수 있도록 v1 필드를 추가·삭제·개명하지 않으며, 변경이 필요하면 AAD v2를 추가합니다.

## 감사 기록 경계

사용자별 `audit` HMAC 체인은 애플리케이션 활동의 변조 탐지용이고, 증거별 Chain of Custody는 증거의 공식 관리 이력입니다. 둘을 유지할 경우 서로 다른 키 파생 목적을 사용하며, Custody 구현이 완료되면 공식 증거 흐름은 Custody를 기준으로 표시합니다.
