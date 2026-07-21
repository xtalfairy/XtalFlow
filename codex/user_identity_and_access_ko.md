# XtalFlow 사용자·계정·권한 설계 초안

작성 기준: 2026-07-20. 이 문서는 레거시 정적 분석과 현재 XtalFlow 구조를 바탕으로 한 설계 기록이며, 운영 서버의 실제 인증 정책이 확인되기 전에는 DB migration이나 로그인 UI의 구현 근거로 단독 사용하지 않는다.

## 1. 결론

XtalFlow의 `User`를 Linux username 하나로 정의하면 안 된다. 다음 identity를 분리한다.

1. **인증 주체(Principal):** 지금 프로그램을 실행하거나 API에 로그인한 주체
2. **XtalFlow 사용자(User):** 프로젝트 권한과 감사 기록의 내부 주체
3. **실험 책임자/소유 프로젝트:** MxLIVE의 user 또는 project와 연결되는 업무상 주체
4. **외부 시스템 계정(External account):** MxLIVE 등 시스템별 identifier와 credential reference

1차 로컬 Viewer에서는 OS 계정을 `LocalOsIdentityProvider`로 읽어 작업자를 식별할 수 있다. 그러나 이것은 편의상 bootstrap identity이지 강한 인증이 아니다. 비밀번호·private key·token은 XtalFlow SQLite에 저장하지 않는다.

## 2. 레거시에서 확인된 사실

| 사실 | 근거 | 심각도 / 작업량 / 회귀 위험 | 권장 검증 |
|---|---|---|---|
| `getpass.getuser()`가 사용자 identity의 출발점이다. | `src_xtalviewer_2_0_legacy/utils/misc.py:118-123` | High / Small / High | 운영 서버에서 실제 UID, login name, sudo·service 실행 결과 비교 |
| root로 실행하면 특정 일반 사용자명으로 강제 치환한다. 인증과 attribution이 틀릴 수 있다. | `src_xtalviewer_2_0_legacy/utils/misc.py:118-123` | **Critical / Small / High** | root/service account 실행 거부 테스트 |
| 사용자명은 `/data/users/<username>`의 홈·기록·설정 경로를 결정한다. | `src_xtalviewer_2_0_legacy/main.py:28`, `main.py:61`, `main.py:2004` | High / Medium / High | 계정별 권한·umask·없는 홈 경로 테스트 |
| 사용자명은 Echo/Shifter 공유 폴더의 하위 디렉터리도 결정한다. | `src_xtalviewer_2_0_legacy/main.py:2190-2192`, `2263-2265`; `utils/func.py:423-425` | High / Medium / High | 장비 운영자와 폴더 naming contract 확인 |
| MxLIVE client는 OS 사용자명을 요청 서명 대상 문자열로 사용한다. | `src_xtalviewer_2_0_legacy/utils/client/getMxlive.py:13-16,44-53`; `putMxlive.py:13-16,49-57` | **Critical / Medium / High** | MxLIVE staging에서 key 등록·폐기·사용자 불일치 contract test |
| MxLIVE key 경로는 `/data/users/<username>/.config/keys.dsa`로 계산된다. key가 없으면 메모리에서 새 DSA key를 만들지만 이 코드에서는 저장·등록 흐름이 보이지 않는다. | `getMxlive.py:14-42`; `putMxlive.py:15-47` | High / Medium / High | 운영 key provisioning 절차와 파일 권한 확인 |
| TLS 인증서 검증이 비활성화되어 있다. | `getMxlive.py:55-60`; `putMxlive.py:59-71` | **Critical / Medium / Medium** | 기관 CA bundle을 사용한 staging TLS 테스트 |
| OS 사용자명을 하드코딩 사전으로 MxLIVE `project_id`에 바꾼다. 알 수 없는 사용자는 `KeyError`가 난다. | `src_xtalviewer_2_0_legacy/utils/adpt.py:88-181` | High / Medium / High | 실제 MxLIVE project 목록과 대조, unknown/disabled user 테스트 |
| worksheet의 MxLIVE record에는 username이 `name`, 매핑 결과가 `project_id`로 들어간다. | `src_xtalviewer_2_0_legacy/utils/func.py:414-420,519-530`; `main.py:2177-2184,2397-2403` | High / Medium / High | 승인된 비식별 golden JSON과 MxLIVE schema 검증 |
| 업로드 실패가 `None`으로 축약되고 사용자·요청 단위 감사 기록은 없다. | `src_xtalviewer_2_0_legacy/utils/client/putMxlive.py:73-89`; `main.py:2243-2256` | High / Medium / High | timeout, 401/403, 409, 5xx 및 재시도 contract test |

## 3. 아직 확인되지 않은 사항

다음은 **추정하지 말고 운영 담당자에게 확인해야 한다.**

- Linux 계정이 개인별인지, 공용 beamline 계정 또는 service account인지
- SSH/LDAP/AD/Keycloak 등 실제 계정 원천과 고유 immutable identifier
- MxLIVE 서명 username이 Linux username과 항상 같은지
- `project_id`가 사람의 ID인지 proposal/group/project의 ID인지
- 한 사용자가 여러 MxLIVE project에 속할 수 있는지와 선택 규칙
- Echo/Shifter 폴더명이 인증·권한 경계인지 단순 routing convention인지
- 여러 사용자가 같은 XtalFlow 프로젝트를 동시에 수정해야 하는지
- target 수정·worksheet 생성·업로드에 필요한 승인 역할과 감사 보존 기간

이 불확실성은 **High / Medium / High**다. 운영 계정 2개 이상과 service account 1개로 end-to-end 관찰하여 확인한다.

## 4. 권장 도메인 모델

```text
AuthenticatedPrincipal
  provider             # local_os, ldap, oidc 등
  subject              # provider가 보장하는 immutable subject
  login_name           # 표시·검색용, 내부 PK로 쓰지 않음
        │ resolves to
        ▼
User
  id                    # XtalFlow UUID
  display_name
  active
        │
        ├── ProjectMembership ── Project
        │     role: owner | editor | viewer
        │
        └── ExternalAccount
              system: mxlive
              external_subject / external_project_id
              credential_ref    # secret 자체가 아닌 참조
```

`Project.owner_id` 하나만 두지 않고 membership을 둔다. 공동 작업, 소유권 이전, 읽기 전용 접근을 자연스럽게 표현할 수 있기 때문이다. 프로젝트에는 owner가 최소 한 명 있어야 한다.

역할별 권한 초안:

| 동작 | owner | editor | viewer |
|---|---:|---:|---:|
| 이미지·target 조회 | 허용 | 허용 | 허용 |
| target 추가·삭제 | 허용 | 허용 | 금지 |
| plate 추가·archive | 허용 | 허용 | 금지 |
| worksheet draft 생성 | 허용 | 허용 | 금지 |
| production 업로드 승인 | 정책 확인 후 허용 | 기본 금지 | 금지 |
| 사용자·역할 관리 | 허용 | 금지 | 금지 |

## 5. 스키마 초안

중앙 협업 단계에서는 PostgreSQL을 원본으로 권장한다. 아래는 개념 스키마이며 현재 SQLite에 즉시 추가하지 않는다.

```sql
users(
  id uuid primary key,
  display_name text not null,
  active boolean not null,
  created_at timestamptz not null
)

user_identities(
  provider text not null,
  subject text not null,
  user_id uuid not null references users(id),
  login_name text,
  last_seen_at timestamptz,
  primary key(provider, subject)
)

project_memberships(
  project_id uuid not null references projects(id),
  user_id uuid not null references users(id),
  role text not null check(role in ('owner', 'editor', 'viewer')),
  created_at timestamptz not null,
  primary key(project_id, user_id)
)

external_accounts(
  id uuid primary key,
  user_id uuid references users(id),
  system text not null,
  external_subject text,
  external_project_id text,
  credential_ref text,
  active boolean not null,
  unique(system, external_subject, external_project_id)
)

audit_events(
  id uuid primary key,
  occurred_at timestamptz not null,
  actor_user_id uuid references users(id),
  actor_provider text not null,
  actor_subject text not null,
  project_id uuid references projects(id),
  action text not null,
  entity_type text not null,
  entity_id text not null,
  correlation_id uuid not null,
  summary_json jsonb not null
)
```

감사 이벤트에는 target의 전체 이미지·worksheet payload·credential을 넣지 않는다. target 변경은 대상 ID와 변경 종류, 필요하면 이전/이후 좌표의 최소 정보만 정책에 맞게 기록한다.

## 6. 현재 로컬 Viewer에 적용할 경계

현재 XtalFlow의 SQLite는 단일 데스크톱 작업공간이며 중앙 인증 저장소가 아니다. 다음 interface만 먼저 고정하는 것이 안전하다.

```python
class IdentityProvider(Protocol):
    def current_principal(self) -> AuthenticatedPrincipal: ...

class AuthorizationPolicy(Protocol):
    def require(self, principal, action, project_id) -> None: ...

class AuditSink(Protocol):
    def append(self, event) -> None: ...
```

- macOS 개발: `LocalOsIdentityProvider`
- Linux 운영 초기: 운영 계정 정책 확인 후 같은 adapter 또는 명시적 로그인 provider
- 중앙화 이후: OIDC/LDAP adapter와 API-side authorization
- MxLIVE: 별도의 `MxliveAccountResolver`와 credential provider

OS username을 project UUID, MxLIVE project ID 또는 filesystem path로 직접 조합하는 코드는 새 domain/application 계층에 두지 않는다.

## 7. 구현 순서

1. **운영 identity contract 확인** — High / Small~Medium / 회귀 없음
2. `AuthenticatedPrincipal`, `IdentityProvider`와 local OS adapter 구현 — Medium / Small / 회귀 Low
3. 새 프로젝트 생성 시 creator attribution을 저장하되 기존 프로젝트는 `unknown/imported`로 명시 migration — Medium / Medium / 회귀 Medium
4. target·plate 변경의 append-only audit event 구현 — High / Medium / 회귀 Medium
5. 중앙 API/PostgreSQL 도입 시 membership과 서버 측 authorization 구현 — High / Large / 회귀 High
6. MxLIVE account/project resolver 및 staging adapter 구현 — Critical / Large / 회귀 High
7. production 업로드 승인 gate와 immutable artifact/delivery audit 구현 — Critical / Large / 회귀 High

## 8. 최소 검증 목록

- 같은 login name이 다른 provider에 존재해도 서로 다른 principal로 처리
- username 변경 후에도 `subject`로 같은 사용자 복원
- unknown, disabled, service, root 계정 처리
- owner/editor/viewer 권한 matrix
- 프로젝트 owner가 0명이 되는 변경 거부
- 두 사용자의 동시 target 수정 conflict
- audit event와 domain 변경의 transaction 일관성
- 로그·DB에 private key, token, 서명 URL 전체가 들어가지 않는지 검사
- MxLIVE external project가 없거나 여러 개인 경우 명시적 선택/오류
- staging에서 TLS 검증, 401/403, timeout, 중복 업로드 검증

## 9. 당장 하지 않을 것

- XtalFlow 전용 비밀번호 저장
- Linux username을 내부 User PK로 사용
- 하드코딩된 username→MxLIVE project ID 사전 이식
- local SQLite만으로 다중 사용자 동시 편집을 지원한다고 간주
- 운영 규칙 확인 전 로그인 창이나 role 관리 UI 구현
- credential 또는 private key를 프로젝트 DB에 저장
