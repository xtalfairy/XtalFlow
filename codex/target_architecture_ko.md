# XtalFlow 목표 아키텍처

> 상태: **Proposed — 구현 기준으로 채택하되 운영 전제 검증 필요**
> 작성일: 2026-07-20
> 대상 Python: 3.12
> 레거시 기준 구현: `src_xtalviewer_2_0_legacy/`
> 목적: 이후 설계·구현·리뷰에서 공통으로 참조할 XtalFlow의 목표 구조를 정의한다.

## 1. 핵심 결정

XtalFlow는 기존 PyQt 단일 프로그램을 확장하는 방식이 아니라, 다음 구조의 새 시스템으로 구축한다.

> **PostgreSQL을 source of truth로 사용하는 중앙 backend를 만들고, UI는 API만 사용한다. Worksheet 생성 결과는 불변 artifact로 보존하며, Echo·Shifter·MxLIVE 전달은 background job과 명시적 상태로 관리한다.**

레거시 프로그램은 새 시스템의 내부 모듈로 재사용하지 않는다. 요구사항 발굴, 기존 출력 비교, 데이터 migration의 근거로만 사용한다.

## 2. 이 결정을 지지하는 현재 근거

- 레거시 프로그램은 RockMaker 이미지, 사용자별 공유폴더, Echo, Shifter, MxLIVE 등 여러 공유 시스템과 연동한다.
- 사용자와 project ID의 대응이 코드에 존재해 단일 개인 도구보다 기관 공용 workflow의 성격이 강하다.
- `targetPoints`, Qt table, worksheet 목록, MxLIVE record가 서로 다른 상태 저장소처럼 사용되고 있다.
- worksheet 생성과 외부 전달의 성공 여부를 중앙에서 재현하거나 감사할 구조가 없다.
- 사용자 PC의 SQLite 파일을 최종 원본으로 두면 동시 작업, 중앙 백업, 권한, 감사, 전달 상태 관리 문제를 나중에 다시 해결해야 한다.

## 3. 전제와 불확실성

다음은 구현 전에 운영 담당자와 확인해야 한다.

1. XtalFlow가 다중 사용자·다중 workstation에서 사용되는가.
2. 중앙 backend와 PostgreSQL을 운영할 서버가 있는가.
3. RockMaker 이미지 storage에 backend가 읽기 접근할 수 있는가.
4. Echo와 Shifter가 공식 API를 제공하는가, 아니면 공유폴더 file drop만 지원하는가.
5. MxLIVE의 현재 API, 인증 방식, TLS CA와 idempotency 계약은 무엇인가.
6. experiment, protein, compound, plate 정보의 데이터 소유권과 보존기간은 무엇인가.
7. 내부망에서 browser UI를 사용할 수 있는가.

정말로 단일 사용자·단일 장비·완전 오프라인 프로그램임이 확인되면 PostgreSQL backend 대신 동일한 domain/application interface 아래 SQLite adapter를 선택할 수 있다. 그러나 UI가 DB를 직접 다루는 구조는 어느 경우에도 채택하지 않는다.

## 4. 시스템 구성

```text
XtalFlow Client
(Web UI 우선 검토, 필요 시 PyQt/PySide client)
        │
        │ HTTPS API
        ▼
XtalFlow Backend
        ├─ API layer
        ├─ Application services
        ├─ Domain model
        ├─ Worksheet compiler
        ├─ Workflow state machine
        ├─ Repository interfaces
        └─ Background worker / transactional outbox
                │
                ├─ RockMaker image adapter
                ├─ Echo delivery adapter
                ├─ Shifter delivery adapter
                └─ MxLIVE adapter
        │
        ▼
PostgreSQL
```

이미지 원본은 PostgreSQL에 binary로 저장하지 않는다. DB에는 이미지의 논리 식별자, storage 위치, checksum, profile, batch, 촬영 시각과 calibration 정보만 저장한다.

## 5. 의존 방향

```text
UI/API
  → Application
    → Domain

Infrastructure
  → Application ports
  → Domain types
```

Domain은 FastAPI, SQLAlchemy, PostgreSQL, PyQt, requests, SMB 경로를 import하지 않는다. Application service는 repository와 외부 system의 interface에만 의존한다. Infrastructure adapter가 interface를 구현한다.

### 금지할 결합

- UI widget 값을 worksheet compiler가 직접 읽는 것
- UI가 PostgreSQL 또는 SQLite에 직접 SQL을 실행하는 것
- domain object가 filesystem path에 파일을 쓰는 것
- API request 처리 중 장시간 SMB write 또는 MxLIVE upload를 동기 수행하는 것
- SQLAlchemy row/dictionary를 UI와 worksheet code 전반에 전달하는 것
- 외부 시스템 응답을 검증하지 않고 domain 상태로 저장하는 것
- `dict[str, Any]`를 핵심 domain schema로 사용하는 것

## 6. 도메인 경계

### 6.1 Identity and projects

상세한 레거시 근거, identity 분리 원칙, 권한 matrix와 스키마 초안은
`codex/user_identity_and_access_ko.md`를 기준 문서로 사용한다.

```text
User
Project
Role
ProjectMembership
```

하드코딩된 username→project ID 매핑을 대체한다. 인증 identity와 실험 책임자 identity를 명시적으로 구분한다.

### 6.2 Imaging

```text
Plate
Well
ImageAcquisition
CrystalImage
ImageCalibration
```

이미지의 출처와 calibration을 관리한다. storage의 절대경로는 domain identifier가 아니라 infrastructure locator다.
현재 Viewer의 well calibration 구현과 pixel→mm 규칙은
`codex/implementation_sequence_ko.md`의 “Well calibration 구현 상태”를 기준으로 한다.

### 6.3 Crystal review

```text
ReviewSession
CrystalCandidate
TargetPoint
ReviewDecision
```

사용자가 어느 이미지와 calibration을 기준으로 어느 target을 선택했는지 관리한다.

### 6.4 Experiment planning

```text
Experiment
TreatmentCondition
Addition
SourceMaterial
CrystalAssignment
```

선택된 crystal, incubation, additive, compound, replica 간 관계를 관리한다. 조건과 crystal의 연결을 UI table의 문자열로 저장하지 않는다.

### 6.5 Worksheet compilation

```text
CompilationRun
EchoTransfer
ShifterInstruction
MxLivePayload
ValidationResult
WorksheetArtifact
```

승인된 experiment revision을 장비별 출력으로 변환한다. 동일한 input snapshot은 결정적으로 동일한 논리 결과를 만들어야 한다.

### 6.6 Delivery

```text
DeliveryJob
DeliveryAttempt
ExternalReference
OutboxEvent
```

artifact가 어떤 외부 시스템에 언제 전달되었고 결과가 무엇인지 기록한다.

## 7. 핵심 식별자와 값 객체

문자열 결합으로 `plate_well`을 표현하지 않는다.

```python
@dataclass(frozen=True)
class PlateRef:
    plate_id: UUID


@dataclass(frozen=True)
class WellRef:
    well_id: UUID


@dataclass(frozen=True)
class PixelPoint:
    x_px: Decimal
    y_px: Decimal


@dataclass(frozen=True)
class TransferVolume:
    shots: int
    shot_volume_nl: Decimal

    @property
    def total_nl(self) -> Decimal:
        return self.shot_volume_nl * self.shots
```

외부에서 사용하는 plate code와 well code는 별도 필드로 유지하고 DB 내부 관계는 UUID 또는 surrogate key로 연결한다.

## 8. 좌표와 calibration 원칙

현재 레거시 구조는 pixel 좌표와 µm 좌표를 함께 저장하지만 어느 calibration으로 변환했는지 보존하지 않는다. 새 구조는 다음을 따른다.

1. 사용자가 클릭한 원본 pixel 좌표를 저장한다.
2. 클릭에 사용된 image ID와 calibration ID를 저장한다.
3. 물리 좌표는 calibration을 이용해 계산한다.
4. worksheet compilation 시 사용한 계산 결과와 calibration version을 input snapshot에 보존한다.
5. calibration이 변경되어도 이미 전달된 artifact의 좌표는 바뀌지 않는다.

```python
@dataclass(frozen=True)
class TargetPoint:
    id: UUID
    crystal_id: UUID
    image_id: UUID
    calibration_id: UUID
    position_px: PixelPoint
    created_by: UUID
    created_at: datetime
```

## 9. Experiment 상태 머신

```text
DRAFT
  ↓
READY_FOR_REVIEW
  ↓
APPROVED
  ↓
COMPILED
  ↓
DELIVERY_PENDING
  ↓
DELIVERED
  ↓
CONFIRMED
```

실패와 종료 상태:

```text
VALIDATION_FAILED
DELIVERY_FAILED
PARTIALLY_DELIVERED
CANCELLED
ARCHIVED
```

### 주요 불변조건

- 승인되지 않은 experiment는 production worksheet로 compile할 수 없다.
- validation error가 있는 compilation은 전달할 수 없다.
- 전달은 특정 experiment revision과 특정 compilation ID를 참조한다.
- 전달된 artifact는 수정하거나 덮어쓰지 않는다.
- 계획 변경 후에는 새 revision과 새 compilation run을 생성한다.
- 한 crystal을 동일 experiment 내 여러 조건에 배정할 수 있는지는 명시적 정책으로 결정한다.
- volume은 음수가 될 수 없고 장비 shot 단위를 만족해야 한다.
- target point는 유효한 image와 calibration을 반드시 참조한다.

## 10. 불변 worksheet artifact

```text
Experiment revision 7
    ↓ compile
Compilation run 12
    ├─ normalized input snapshot
    ├─ validation report
    ├─ Echo artifact
    ├─ Shifter artifact
    ├─ MxLIVE payload artifact
    ├─ SHA-256 checksums
    └─ compiler version
```

experiment가 변경되면 기존 파일을 덮어쓰지 않고 새 compilation run을 만든다. DB에는 artifact metadata와 checksum을 저장한다. 파일 자체를 DB에 둘지 object/file storage에 둘지는 운영 환경을 확인해 결정한다.

## 11. 외부 전달

사용자 요청은 DB transaction 안에서 delivery job과 outbox event를 만든 뒤 즉시 반환한다. Worker가 외부 전달을 수행한다.

```text
Deliver command
  → 권한과 상태 검증
  → DeliveryJob 저장
  → OutboxEvent 저장
  → transaction commit
  → worker가 event 처리
  → Echo / Shifter / MxLIVE별 DeliveryAttempt 기록
```

자동 retry는 외부 시스템의 idempotency가 확인된 경우에만 허용한다. 각 adapter는 timeout, retry 가능 오류, 영구 오류, 인증 오류를 구분해야 한다.

## 12. 데이터베이스 개념 모델

```text
users
projects
project_memberships

experiments
experiment_revisions
treatment_conditions
additions
crystal_assignments

plates
wells
image_acquisitions
crystal_images
image_calibrations
review_sessions
crystal_candidates
target_points
review_decisions

compilation_runs
validation_messages
worksheet_artifacts

delivery_jobs
delivery_attempts
external_references
outbox_events
audit_events
```

실제 SQL schema는 workflow와 cardinality 확인 후 작성한다. 이 목록을 그대로 테이블로 기계적으로 생성하지 않는다.

## 13. API 원칙

- API는 versioned schema를 사용한다.
- client가 DB key나 내부 storage path를 조합하지 않는다.
- command와 query를 구분한다.
- experiment update에는 revision을 포함해 optimistic locking을 적용한다.
- API 오류는 사용자 입력, conflict, authorization, 외부 system, 내부 오류를 구분한다.
- list endpoint는 pagination과 filtering을 제공한다.
- worksheet delivery와 같은 명령은 idempotency key를 받는다.
- audit에 필요한 actor, timestamp, correlation ID를 보존한다.

예시 command:

```text
CreateExperiment
RegisterPlate
StartReviewSession
SelectCrystalTarget
RemoveCrystalTarget
AssignCrystalToCondition
SubmitExperimentForReview
ApproveExperiment
CompileExperiment
RequestDelivery
```

## 14. UI 결정

### 우선 검토안

Web UI를 우선 평가한다.

- 중앙 배포와 업데이트
- macOS/Linux client 환경 차이 감소
- 인증과 권한 통합
- 공동 작업과 작업 복구
- browser canvas/WebGL 기반 이미지 target 편집 가능

### Desktop client 선택 조건

다음이 확인되면 PySide6/PyQt6 client를 선택할 수 있다.

- browser가 접근할 수 없는 이미지 storage
- 특수 입력장치 또는 로컬 장비 제어
- 오프라인 운용 요구
- 고해상도 이미지 처리 성능이 browser에서 충족되지 않음

Desktop client도 HTTPS API만 사용한다. 로컬 SQLite는 cache 또는 offline queue로만 사용할 수 있고 중앙 원본으로 간주하지 않는다.

## 15. 기술 후보

### Backend

- Python 3.12
- FastAPI
- Pydantic
- SQLAlchemy 2
- Alembic
- PostgreSQL
- background worker와 transactional outbox
- 구조화 logging과 metrics

### Frontend

- 1안: TypeScript 기반 Web UI와 Canvas/WebGL image viewer
- 2안: PySide6/PyQt6 API client

구체적인 frontend framework와 worker 제품은 작은 기술 검증 후 결정한다. 도메인과 API가 특정 framework에 결합되지 않게 한다.

## 16. 테스트 전략

```text
Domain unit tests
  ├─ plate/well 규칙
  ├─ 좌표 변환
  ├─ volume/shot 계산
  ├─ experiment 상태 전이
  └─ assignment 불변조건

Application tests
  ├─ repository fake
  ├─ command authorization
  ├─ optimistic locking
  └─ outbox 생성

Integration tests
  ├─ PostgreSQL repository
  ├─ migration
  ├─ API contract
  └─ fake external systems

Artifact tests
  ├─ Echo golden files
  ├─ Shifter golden files
  ├─ MxLIVE payload fixtures
  └─ deterministic checksums

UI tests
  ├─ image navigation
  ├─ target selection/removal
  ├─ condition assignment
  └─ review/approval flow
```

production MxLIVE, Echo, Shifter를 자동 테스트 대상으로 사용하지 않는다. 비식별 fixture와 fake/staging adapter를 사용한다.

## 17. 보안과 운영 원칙

- TLS 검증을 비활성화하지 않는다.
- 비밀번호, token, private key, CA material을 Git이나 일반 DB field에 저장하지 않는다.
- secret manager 또는 제한된 운영 secret file을 사용한다.
- 최소 권한과 project-based authorization을 적용한다.
- 실험 metadata와 사용자 action을 audit하되 민감한 payload 전체를 log하지 않는다.
- structured log에 correlation ID, experiment ID, compilation ID, delivery ID를 포함한다.
- backup, restore, migration rollback과 disaster recovery를 운영 설계에 포함한다.

## 18. 레거시와의 관계

레거시 프로그램은 다음 역할만 가진다.

- 사용자 workflow와 숨은 규칙을 찾는 참고 구현
- worksheet golden output 생성
- migration 대상 JSON/CSV schema 확인
- 새 compiler와 결과 비교
- 비정상·경계 사례 발굴

레거시 dictionary를 새 DB table에 그대로 옮기지 않는다. 별도 migration adapter가 읽고 validation과 normalization을 거쳐 새 API로 입력한다.

```text
Legacy JSON/CSV
  → Legacy DTO
  → validation report
  → normalized import command
  → Application service
  → PostgreSQL
```

## 19. 구현 순서

최소 변경이 아니라 목표 구조의 의존 관계에 따른 순서다.

1. 운영 전제와 외부 system contract 확정
2. domain 용어와 불변조건 승인
3. architecture skeleton과 dependency rule 구축
4. PostgreSQL schema·migration·repository 구축
5. crystal review와 target/calibration domain 구현
6. experiment planning과 assignment 구현
7. worksheet compiler와 golden test 구현
8. artifact/versioning 구현
9. outbox, worker, 외부 adapter 구현
10. Web UI와 desktop client 기술 검증 후 client 구현
11. legacy migration/import 도구 구현
12. staging parallel run과 운영 전환

## 20. 아키텍처 완료 조건

- UI 없이 API와 domain test만으로 experiment 생성부터 compilation까지 검증할 수 있다.
- 같은 normalized input과 compiler version은 같은 논리 output을 만든다.
- 모든 worksheet와 delivery가 experiment revision에 추적된다.
- production 전달 실패가 DB에 명시적으로 남고 재처리 여부를 판단할 수 있다.
- 사용자 PC가 손실되어도 중앙 data와 작업 이력이 복구된다.
- 레거시 `targetPoints`와 Qt table에 의존하지 않는다.
- 외부 adapter를 fake로 교체해 전체 workflow integration test가 가능하다.
- TLS 검증, secret 관리, authorization, audit 정책을 만족한다.

## 21. 이후 작업에서의 사용 규칙

이 문서는 이후 구현의 기본 방향이다. 다음에 해당하는 변경은 이 문서와 함께 검토한다.

- 중앙 backend 대신 client-local DB를 source of truth로 만드는 변경
- UI의 직접 DB 접근
- worksheet artifact 덮어쓰기
- 외부 전달을 동기 request에 결합
- domain에 PyQt/FastAPI/SQLAlchemy/filesystem 의존성을 추가
- calibration 없이 물리 좌표를 저장하거나 재계산
- experiment revision과 무관한 worksheet 생성

운영 전제 확인으로 결정이 바뀌면 이 문서를 조용히 덮어쓰지 않는다. 변경 이유, 대안, 영향과 승인일을 별도 architecture decision record로 남긴다.
