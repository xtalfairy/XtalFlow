# XtalFlow 구현 순서

> 상태: Active
> 작성일: 2026-07-20
> 목표 아키텍처: `codex/target_architecture_ko.md`
> 원칙: 레거시 구현 구조를 복제하지 않고, XtalViewer에서 XtalFlow로 발전한 기능의 역사적 순서를 따른다.

## 제품의 근간

XtalFlow의 첫 제품은 **RMServer에서 crystal image를 찾아 표시하고, 사용자가 crystal well과 target 좌표를 검토·선택하는 Viewer**다. Experiment planning, worksheet, 외부 전달은 이 결과를 소비하는 후속 기능이다.

## Phase 1 — Viewer foundation

상태: **완료** (2026-07-20)

범위:

- plate code로 RMServer plate directory 해석
- 숫자가 가장 큰 유효 `batchID_*` 선택
- `wellNum_* / profileID_* / d*_..._ef.jpg` 원본 이미지 탐색
- thumbnail과 비정상 디렉터리 제외
- plate/well/drop navigation
- 원본 비율을 유지한 이미지 표시

완료 조건:

- 실제 fixture의 plate 1069, 1100, 2069, 2070을 읽을 수 있다.
- 실제 RMServer에서 관찰된 `plateID_<plate>`와 `plateID<plate>` 두 이름 형식을 모두
  입력 형식으로 보존하고 지원한다. `1070`과 `2070`은 별개의 plate다.
- 최신 batch를 결정적으로 선택한다.
- 원본 `ef`만 사용하고 `_th`, `_th_low`, composite를 제외한다.
- RMServer가 없어도 임시 filesystem fixture로 모든 탐색 규칙을 테스트한다.
- UI를 offscreen 환경에서 생성하고 이미지 전환을 검증할 수 있다.

이 단계에서 하지 않는 것:

- target 좌표 선택
- DB
- chemical/treatment plan
- worksheet
- MxLIVE/Echo/Shifter 연결

## Phase 2 — Target review

상태: **진행 중** — 원본 pixel 기반 target 추가·표시·삭제, SQLite snapshot 저장,
보조 auto-next 기준 충족 시 자동 이동, 좌우 화살표 이동 구현

구조 정리 1단계 완료 (2026-07-20):

- `ReviewProgress`와 `ReviewPreferences` 책임 분리
- 사용되지 않던 `dirty` 상태 제거
- GUI 및 저장소와 독립적인 `ReviewController` 도입
- target 조작, auto-next 판단, navigation 상태 변경을 controller로 이동
- SQLite schema와 기존 저장 데이터 형식은 유지

구조 정리 2단계 완료 (2026-07-20):

- application 계층에 `ReviewStorePort`와 `ReviewPersistenceError` 도입
- 이미지 target snapshot과 다음 progress 위치를 단일 transaction으로 저장
- checkpoint 실패 시 DB transaction과 메모리 navigation을 함께 rollback
- SQLite migration을 별도 모듈로 분리하고 `PRAGMA user_version` 적용
- 기존 `required_target_count` column을 `auto_advance_target_count`로 보존 migration
- DB 열기·읽기·쓰기 실패를 사용자 오류로 변환하고 connection close를 idempotent하게 처리

구조 정리 3단계 완료 (2026-07-20):

- `image_label` 등 과거 QLabel 기반 이름을 실제 UI 책임에 맞게 변경
- controller가 현재 이미지의 unsaved working-state를 추적
- UI에 `Saved`, `Unsaved changes`, `Save failed` 상태 표시
- 종료 checkpoint 실패 시 Retry, Discard, Cancel 선택 제공
- Cancel 시 창과 DB connection을 유지하여 데이터 유실 방지
- 실제 RMServer fixture offscreen render와 focus·navigation·복구 회귀 검증

범위:

- image와 calibration에 연결된 target point
- target 추가·삭제
- 이미지 이동 직전 현재 이미지 target을 단일 SQLite transaction으로 저장
- `auto_advance_target_count`는 검토 완료 조건이 아닌 작업 편의 설정이다.
- 실제 선택 개수가 auto-next 기준에 도달하면 저장 후 다음 이미지로 이동한다.
- 기준보다 적거나 많은 target도 유효하며 수동 이전/다음 이동을 막지 않는다.
- 이미지별 실제 target 개수는 `TargetPoint`에서 계산하고 중복 저장하지 않는다.
- 향후 soaking volume은 `final_volume / 실제 target 개수`로 계산하며 auto-next
  기준값을 계산에 사용하지 않는다. 실제 target이 0이면 계산 불가로 검증한다.

### 재검토와 jump/navigation 원칙

FBDD image 검토는 한 번의 최종 판정 과정이 아니다. 처음에는 target을 지정하지
않았던 이미지를 다시 보거나, target을 지정한 이미지만 반복 검토하는 흐름을 정상
사용 사례로 취급한다. 따라서 `NO_CRYSTAL`, `REVIEWED`, `COMPLETED` 같은 terminal
상태를 검토 도중 강제하지 않는다.

plate의 전체 image tuple과 RMServer 정렬 순서를 canonical navigation source로
유지하고, 다음 조건은 복제 목록이 아닌 query/filter로 제공한다.

- `ALL`: 전체 이미지
- `WITH_TARGETS`: 현재 하나 이상의 `TargetPoint`가 있는 이미지
- `WITHOUT_TARGETS`: 현재 `TargetPoint`가 없는 이미지
- `UNVISITED`: 아직 화면에 표시된 적 없는 이미지
- `BOOKMARKED`: 사용자가 다시 보기로 표시한 이미지

필요한 명령:

- 현재 조건의 이전/다음 이미지로 jump
- well/drop을 지정하여 직접 jump
- target이 있는 첫/마지막 이미지로 jump
- 최근 방문 이미지로 복귀

일반 Previous/Next와 auto-next는 canonical 전체 순서를 유지한다. 필터 기반 jump는
별도 명령으로 두어 필터 변경 때문에 일반 이동 의미가 바뀌지 않게 한다. 현재
이미지에서 target을 전부 삭제하면 `WITH_TARGETS` query에서 즉시 제외되고
`WITHOUT_TARGETS` query에 포함되어야 한다.

`target_count`와 `has_targets`는 `TargetPoint`에서 계산하며 별도 mutable column으로
중복 저장하지 않는다. `visited_at`과 bookmark는 target과 다른 사용자 탐색 metadata로
저장한다. worksheet 후보는 생성 시점의 target snapshot으로 결정하며 방문 여부나
bookmark를 실험 대상 판정에 사용하지 않는다.

### Navigator 선행 조건: project와 다중 plate

실제 작업 단위는 단일 plate가 아니라 여러 plate를 포함한 project인 경우가 많다.
따라서 navigator UI보다 먼저 project aggregate와 plate membership을 구현한다.

권장 계층:

```text
Project
  ├─ ProjectPlate (정렬 순서, plate code, 선택 batch/profile)
  │    └─ PlateImages
  │         └─ CrystalImage
  │              └─ TargetPoint
  └─ active plate / active image
```

`ProjectPlate`는 단순 plate code 목록이 아니다. 검토 재현성을 위해 project에 추가할
때 선택된 RMServer batch와 profile을 함께 고정한다. RMServer에 더 최신 batch가 생겨도
기존 project가 조용히 다른 이미지 집합으로 바뀌어서는 안 된다. 최신 batch로 전환은
사용자의 명시적 upgrade 작업으로 처리한다.

다중 plate 관리 UI의 최소 기능:

- project 생성·열기·이름 변경
- plate code 입력으로 project에 plate 추가
- plate 제거 전 target 존재 여부와 영향 확인
- plate 순서 변경
- plate별 batch/profile, image 수, target 수 표시
- 현재 plate 선택과 plate 간 이동
- 마지막 active plate/image 복구

navigator query의 scope는 `CURRENT_PLATE`와 `WHOLE_PROJECT`를 명시적으로 구분한다.
일반 Previous/Next와 auto-next는 기본적으로 현재 plate 안에서만 동작한다. 마지막
이미지에서 다음 plate로 자동 이동하는 동작은 별도 project preference로 두어 암묵적인
plate 경계 이동을 피한다. `WITH_TARGETS`, `WITHOUT_TARGETS`, `UNVISITED`,
`BOOKMARKED` jump는 현재 plate 또는 project 전체 scope 중 하나를 선택할 수 있어야 한다.

기존 단일 plate SQLite review는 삭제하거나 덮어쓰지 않는다. project schema 도입 시
기존 review를 보존하고, 사용자가 project에 plate를 추가할 때 연결하거나 명시적인
`Imported standalone reviews` project로 migration한다.
- 좌우 화살표 키를 통한 이전/다음 이미지 이동
- well별 review 상태
- review session 저장·복구
- 기존 XtalViewer와 좌표 비교

완료 조건:

- scaling 또는 창 크기 변경 후에도 동일한 원본 pixel을 가리킨다.
- pixel→µm 변환이 versioned calibration으로 재현된다.
- 저장 후 복구한 target이 동일 위치에 표시된다.
- `targetPoints`와 `targetCounts` 같은 중복 mutable state를 사용하지 않는다.

## Phase 3 — Experiment planning

범위:

- selected crystal
- treatment condition, addition, incubation, replica
- crystal-condition assignment
- plan validation과 revision

완료 조건:

- UI table 없이 domain object만으로 유효한 plan을 구성한다.
- volume/shot, plate/well, assignment 불변조건을 자동 테스트한다.

## Phase 4 — Worksheet compilation

범위:

- Echo transfer
- Shifter instruction
- MxLIVE payload
- validation report와 immutable artifact

완료 조건:

- production에 쓰지 않고 preview/local export가 가능하다.
- 승인된 legacy golden output과 비교한다.
- 같은 normalized input과 compiler version은 같은 결과를 만든다.

## Phase 5 — Delivery

범위:

- Echo/Shifter/MxLIVE adapter
- background job, outbox, delivery attempt
- timeout, idempotency, partial failure, audit

완료 조건:

- staging contract test를 통과한다.
- 어떤 revision/artifact가 어디에 전달됐는지 재현할 수 있다.
- production 전달에는 명시적 승인 gate가 있다.

## Phase 6 — Central collaboration

범위:

- 중앙 API와 PostgreSQL
- authentication/authorization
- multi-user review, optimistic locking, approval
- migration과 운영 배포

초기 Viewer의 domain/application interface는 유지하고 local adapter를 중앙 repository/API adapter로 교체한다.

## 변경 규칙

- 현재 phase의 완료 조건을 통과하기 전 다음 phase 기능을 UI에 섞지 않는다.
- 외부 시스템의 숨은 규칙이 발견되면 먼저 문서와 test fixture에 반영한다.
- 목표 아키텍처와 이 순서가 충돌하면 구조는 목표 아키텍처를, 범위 순서는 이 문서를 따른다.
- 중요한 방향 변경은 architecture decision record로 남긴다.
