# XtalFlow 안전한 현대화 로드맵

> 원칙: 기능 변경과 구조 변경을 분리하고, worksheet/업로드처럼 실험 결과에 영향을 주는 경로는 golden test와 staging contract가 생기기 전 수정하지 않는다. 아래 작업량과 위험은 현 정적 분석 기준 추정이다.
> 경로 변경: 분석 후 레거시 `src/` 전체가 `src_xtalviewer_2_0_legacy/`로 이동되었다. 아래의 `src/...` 근거 경로는 모두 새 디렉터리 아래의 동일 상대 경로를 뜻한다.
> 목표 구조: 이후 신규 구현은 `codex/target_architecture_ko.md`를 기본 아키텍처 기준으로 사용한다. 이 로드맵의 초기 점진 리팩터링 제안과 목표 아키텍처가 충돌하면 목표 아키텍처를 우선하고, 레거시는 요구사항·golden output 확인용으로만 사용한다.

## Phase 0 — 즉시 통제할 치명적 문제

| 순서 | 작업 | 근거 | 심각도 / 작업량 | 회귀 위험 | 검증/완료 조건 |
|---:|---|---|---|---|---|
| 1 | MxLIVE TLS 검증 우회 사용 중단 계획과 CA 확인 | `getMxlive.py:1-3,55-60`, `putMxlive.py:1-3,59-71` | Critical / Small~Medium | High | staging에서 valid/invalid/expired/hostname mismatch 인증서 테스트 |
| 2 | 1024-bit DSA 서명·key 저장 정책을 MxLIVE 관리자와 공동 점검 | clients의 `get_keys()` 및 `signing.py:15-56` | Critical / Medium | High | 새/기존 client 상호운용, rotation/권한/실패 복구 테스트 |
| 3 | root→특정 사용자 강제 치환 제거 설계 | `misc.py:118-123` | Critical / Small | High | root/service account 실행 거부 또는 명시 identity 테스트 |
| 4 | worksheet와 업로드를 운영에서 검증 가능한 단위로 기록 | CSV/JSON 직접 write와 record 전체 print (`misc.py:62-94`, `func.py:480-553`) | Critical / Medium | Medium | correlation ID, 민감필드 redaction, 파일 checksum, 성공/실패 상태 확인 |
| 5 | 민감 데이터 저장소 정책 결정 | 두 `labwork.json`, account mapping, chemical CSV/PNG | High / Small~Medium | Low~High | 데이터 소유자 승인, 비식별 fixture, repository visibility audit |

이 단계에서 서비스 protocol을 독단적으로 바꾸면 안 된다. 보안 수정이 장비/서버 호환성을 깨뜨릴 수 있으므로 staging과 rollback 가능한 배포가 필수다.

## Phase 1 — 실행 환경 재구성

1. **현 운영 baseline 동결:** Python/OS/CPU, pip freeze, Qt/GLIBC, mount 옵션, CA, env, 실행 command를 수집한다. `run_program.sh:1-5`와 `main.py:26-35`에 숨은 전제를 문서화한다. **High / Small / 회귀 Low**.
2. **Python 3.11 clean environment:** 활성 import 기준 최소 dependency만 설치한다. top-level requirements와 `mxdc/requirements.txt`는 분리한다. **High / Medium / 회귀 High**.
3. **환경 설정 외부화:** install root, user data root, RockMaker/Echo/Shifter, MxLIVE endpoint, beamline을 typed configuration으로 만들되 기본값과 validation을 둔다. **High / Medium / 회귀 High**. 검증은 Linux/macOS configuration matrix.
4. **재현 가능한 실행점:** 저장소/설치 위치와 무관한 package entry point를 만들고, Qt 자산을 package resource로 다룬다. **High / Medium / 회귀 Medium**.
5. **CI skeleton:** syntax/import, unit tests, Python 3.11, Linux headless를 먼저 두고 macOS smoke를 추가한다. **High / Medium / 회귀 Low**.

완료 조건: 새 Linux clean venv에서 외부 mount 없이 fake service/fixtures로 앱이 시작되고, 최소 regression suite가 통과하며, 기존 운영환경은 그대로 rollback 가능해야 한다.

## Phase 2 — 리팩터링 전 회귀 안전망

| 묶음 | 최소 테스트 | 심각도 / 작업량 | 회귀 위험 | 검증 방법 |
|---|---|---|---|---|
| Domain mapping | plate ID parse, 2d/3d→384 mapping, volume/shot 분배 | High / Medium | Low | table/golden tests; `main.py:492-517`, `adpt.py:6-84`, `main.py:1922-1988` |
| Filesystem | latest batch, profile, missing/corrupt image, permissions | High / Medium | Medium | temporary tree; `func.py:63-210` |
| Artifacts | 모든 workflow의 Echo/Shifter CSV와 MxLIVE JSON | Critical / Large | High | 운영 승인 golden set + 공식 schema/단위/좌표 검증 |
| Network | sign/get/post, timeout, TLS, 4xx/5xx, retry/idempotency | Critical / Medium | High | local fake server + staging contract |
| UI | startup, plate load, target add/remove, export/import | High / Medium | Medium | pytest-qt/offscreen와 제한된 수동 acceptance |

테스트 fixture에는 실사용자명, protein, compound, plate/path를 넣지 않는다. golden 결과는 데이터 소유자가 비식별성을 확인해야 한다.

## Phase 3 — 기능을 유지하며 구조 개선

목표 구조는 다음 책임 경계를 권장한다.

```text
UI (PyQt widgets/controllers)
  -> Application services (review plate, plan experiment, generate worksheet, upload)
     -> Domain (plate/well/target/volume/worksheet models + validation)
     -> Ports (image repository, worksheet sink, LIMS client, identity provider)
        -> Adapters (RockMaker filesystem, Echo/Shifter shares, MxLIVE HTTP)
```

1. `MyApp`에서 순수 계산부터 추출한다: ID parsing, coordinate conversion, shot distribution, worksheet row construction. **High / Large / 회귀 High**. 각 추출은 기존/신규 결과를 같은 fixture로 비교한다.
2. Qt widget 값을 직접 읽는 대신 immutable request model을 만든다. **High / Medium / 회귀 High**.
3. filesystem과 MxLIVE를 interface 뒤로 이동하고 timeout, typed error, retry 정책을 정의한다. 업로드 재시도는 서버 idempotency 확인 전 자동화하지 않는다. **Critical / Large / 회귀 High**.
4. CSV/JSON write를 임시파일+fsync+atomic rename 패턴으로 바꾸고 manifest/checksum을 만든다. 네트워크 share의 rename 원자성은 실환경에서 검증한다. **Critical / Medium / 회귀 High**.
5. UI long task를 worker로 옮기고 cancel/progress/error를 표시한다. Qt object는 main thread 밖에서 직접 만지지 않는다. **High / Medium / 회귀 Medium**.
6. `print`를 구조화 로깅으로 바꾸고 개인정보/실험 필드를 기본 redaction한다. **High / Medium / 회귀 Low**.

## Phase 4 — 중복과 디렉터리 정리

이 단계는 Phase 2 테스트와 운영 사용처 조사가 끝난 뒤 수행한다.

1. `src/utils/client/mxdc`와 `src/utils/utils/client/mxdc`는 295개 파일이 byte-for-byte 동일하다. 외부 참조가 없으면 중첩본 제거, MXDC 자체가 불필요하면 두 vendor tree 제거를 검토한다. **High / Medium / 회귀 Medium~High**.
2. `src/utils/utils`의 변경된 `adpt.py`, `func.py`, `misc.py`, `signing.py` 차이를 ADR로 남긴 뒤 obsolete tree를 제거한다. **High / Medium / 회귀 Medium**.
3. 과거 main variants를 Git tag/문서로 보존하고 실행 사용처가 없음을 확인한 뒤 제거한다. **Medium / Medium / 회귀 High**.
4. `.pyc`, `__pycache__`, `.swp`, `.bak`, 임시/이상명 파일을 clean-up하고 ignore 정책을 확인한다. **Low / Small / 회귀 Low**.
5. chemical/image/Backdrop 자산은 기능과 데이터 보존 정책에 따라 별도 versioned data package 또는 외부 storage로 이동을 검토한다. **Medium / Large / 회귀 High**.

## Phase 5 — 나중에 해도 되는 정리

- 명명(`evalue`, XtalViewer 문자열), 주석, formatting, type hints를 기능 테스트 이후 정리. **Low / Medium / 회귀 Low**.
- no-op method와 commented-out block 제거. **Low / Small~Medium / 회귀 Low~Medium**.
- `os.path`를 `pathlib`, wildcard Qt imports를 explicit imports로 점진 전환. **Low~Medium / Medium / 회귀 Medium**.
- plate grid의 거대한 literal을 생성 규칙/검증된 data table로 변경. **Medium / Medium / 회귀 High**; 전수 mapping golden test가 선행되어야 한다.

## 제거·교체를 검토할 오래된 기술

| 기술 | 권고 | 근거 | 위험/검증 |
|---|---|---|---|
| Python 3.7 | Python 3.11 baseline, 이후 3.13 | `run_program.sh:3`, 3.7 EOL | High / Large / High; dual-run golden comparison |
| exact 2020~2021 scientific pins | 활성 최소 dependency로 재선정 | `requirements.txt:1-43` | High / Large / High; clean wheel matrix |
| PyQt5/Qt5 | 1차는 유지·갱신, 장기 PyQt6/PySide6 평가 | UI 전체가 PyQt5에 강결합 | Medium / Large / High; 별도 spike |
| 1024-bit DSA URL signing | 서버와 공동 교체 | client key/signing 코드 | Critical / Large / High; protocol contract |
| TLS `verify=False` | 신뢰 CA 기반 검증으로 교체 | client HTTP functions | Critical / Medium / High |
| vendored MXDC full source | dependency/별도 repo/제거 | 활성 import 없음, 두 완전 복제 | Medium / Medium / Medium~High |
| 파일 drop 장비 통합 | 공식 API가 있으면 adapter로 교체 검토 | Echo/Shifter share에 CSV 직접 기록 | Critical / Large / High; 장비 vendor acceptance |

## 권장 릴리스 전략과 우선 작업 5개

브랜치별 작은 변경, dual-run, staging, 사용자가 확인할 artifact diff, 즉시 rollback을 기본으로 한다. DB/장비에 쓰는 acceptance test는 자동으로 production을 향하지 않게 별도 승인 gate를 둔다.

가장 먼저 할 다섯 작업은 다음과 같다.

1. 운영 실행환경과 외부 contract(MxLIVE, RockMaker, Echo, Shifter)를 문서화하고 소유자를 지정한다.
2. TLS/DSA/root identity와 민감 로그·fixture의 보안 조치를 staging 계획으로 확정한다.
3. 비식별 fixture로 plate mapping, worksheet CSV/JSON, MxLIVE client P0 regression test를 만든다.
4. Python 3.11 최소 dependency clean environment와 Linux headless smoke CI를 만든다.
5. `MyApp`에서 worksheet/domain 계산을 UI와 분리하기 시작하되 매 단계 golden diff를 통과시킨다.

## 의사결정이 필요한 불확실성

- 어떤 legacy entry point와 rank/MXDC 기능이 실제 사용자에게 필요한가.
- worksheet column, 단위, 좌표, filename, 전달 원자성의 공식 규격은 무엇인가.
- MxLIVE가 지원하는 현대 인증 방식과 인증서 배포 주체는 누구인가.
- 운영 Linux/CPU와 macOS 개발기 architecture는 무엇인가.
- chemical/labwork data를 Git에 둘 법적·기관 정책상 권한이 있는가.

이 다섯 항목이 해소되기 전에는 대규모 삭제, dependency 일괄 upgrade, UI framework 교체, production upload retry 자동화를 진행하지 않는 것이 안전하다.
