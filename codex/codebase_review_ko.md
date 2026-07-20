# XtalFlow 레거시 코드베이스 리뷰

> 분석 기준일: 2026-07-20
> 범위: 저장소의 기존 파일은 변경하지 않고 정적·읽기 전용으로 분석했다. 실제 빔라인 마운트, MxLIVE, Echo/Shifter 장비에는 접속하지 않았다.
> 표기: **사실**은 코드/파일에서 직접 확인한 내용, **추정**은 실행 환경이나 운영 관행을 확인해야 하는 판단이다.
> 경로 변경: 분석 후 레거시 `src/` 전체가 `src_xtalviewer_2_0_legacy/`로 이동되었다. 아래의 `src/...` 근거 경로는 모두 새 디렉터리 아래의 동일 상대 경로를 뜻한다.

## 1. 요약과 판정 기준

이 저장소는 패키지화된 다계층 애플리케이션이 아니라, `src/main.py`에 PyQt5 UI와 상태, 이미지 탐색, 실험 계획, worksheet 생성, MxLIVE 업로드가 집중된 레거시 데스크톱 프로그램이다. 현재 공식 실행 스크립트로 보이는 `src/run_program.sh`는 작업 디렉터리가 `src/`라는 전제, `.xv_venv`, Python 3.7 공유 라이브러리, `/usr/local/XtalViewer/2.0`, `/data/users`, `/smbmount`를 전제로 한다.

심각도는 데이터 손실·잘못된 장비 지시·인증 우회 가능성을 `Critical`, 핵심 기능 불능이나 중대한 운영 장애를 `High`, 유지보수/일부 기능 장애를 `Medium`, 정리·가독성 문제를 `Low`로 분류했다. 작업량은 대략 1일 이내 `Small`, 수일~2주 `Medium`, 여러 모듈/외부 시스템 협업이 필요한 작업을 `Large`로 본다.

## 2. 프로그램 구조와 실행 흐름

### 주요 경로

| 경로 | 확인된 역할 | 상태 |
|---|---|---|
| `src/main.py:1-2820` | 현재 주 진입점. `index`, `MyApp`, 대화상자와 검증 위젯 포함 | **사실** |
| `src/run_program.sh:1-5` | `.xv_venv` 활성화, `/opt/python37/lib` 지정 후 `python main.py` | **사실** |
| `src/utils/func.py:13-663` | plate 경로 탐색·이미지 인벤토리·worksheet/JSON 생성·완료정보 병합 | **사실** |
| `src/utils/misc.py:8-351` | 디렉터리/CSV/JSON/config/library/table 변환 유틸리티 | **사실** |
| `src/utils/adpt.py:6-188` | plate well 변환과 사용자→project ID 매핑 | **사실** |
| `src/utils/chem.py:13-` | RDKit 기반 화학구조 이미지 생성 | **사실** |
| `src/utils/client/getMxlive.py:1-96` | MxLIVE labwork 조회 | **사실** |
| `src/utils/client/putMxlive.py:1-108` | MxLIVE JSON 업로드 | **사실** |
| `src/utils/client/signing.py:15-56` | DSA 기반 URL 서명 | **사실** |
| `chems/*.csv` | 화합물 라이브러리. 총 10개 CSV, 헤더에 vendor/ID/SMILES/plate/well 등 | **사실** |
| `chems/UC-192873/*/*.png` | 화합물별 구조 이미지로 보이는 109개 파일 | **추정** |
| `img/`, `data/` | UI 이미지와 legacy ranking backdrop 자산 | **사실** |
| `src/main2.py`, `eval.py`, `evalue.py`, `sv2.py`, `sv3.py`, `pret.py`, `simply.py`, `evaSrc/main.py` | 서로 상당 부분 겹치는 과거/실험 진입점 후보 | **추정** |
| `src/new_main.py:1-71` | asyncio와 adapter/orchestrator 인터페이스를 스케치한 프로토타입 | **사실** |
| `src/utils/client/mxdc/` | 외부 MXDC 전체 소스·문서·GTK UI·배포 스크립트 | **사실** |
| `src/utils/utils/` | `src/utils/`의 과거 중첩 복사본. 일부 파일은 구버전 | **사실** |

### 시작점과 실행 방법

- **사실:** `src/run_program.sh:2-4`는 `src/`에서 `bash run_program.sh`로 실행해야 상대 경로가 맞는다. 저장소 루트에서 실행하면 `.xv_venv`와 `main.py`를 찾지 못한다.
- **사실:** 직접 실행은 `cd src && python main.py` 형태이며 `src/main.py:2810-2820`에서 `QApplication`, `index`를 생성하고 Qt event loop에 진입한다.
- **사실:** 현재 이름은 창 제목과 기록에 여전히 Xtal Viewer/XtalViewer로 남아 있다(`src/main.py:46`, `src/main.py:2178`, `src/main.py:2398`).
- **사실:** `src/main.py:27-35`의 설치 위치와 자산 경로가 `/usr/local/XtalViewer/2.0`에 고정되어 있으므로 checkout 위치에서 곧바로 실행되는 구조가 아니다. 창 아이콘 경로도 `src/main.py:47`에서 구분자 하나가 빠져 있을 가능성이 있다.

### 전체 실행 흐름

1. 모듈 import 시 사용자명을 읽고 `/data/users/<user>` 및 네 개의 `/smbmount` 경로를 전역 `PATHS`로 만든다 (`src/main.py:26-37`).
2. `index`가 `MyApp`을 만들고, `MyApp.initUI()`가 약 212줄에 걸쳐 UI 위젯과 모든 mutable state를 준비한다 (`src/main.py:39-313`).
3. 사용자가 plate ID를 입력하면 `processPlateIDs()` → `loadPlateInfo()` → `func.readPlate()`가 RockMaker 디렉터리의 최신 `batchID_*`와 `profileID_*` 이미지를 스캔한다 (`src/main.py:492-566`, `src/utils/func.py:63-210`). 결과 요약 JSON도 사용자 cellar에 저장한다.
4. 이미지를 `QPixmap/QPainter`로 표시하고 well 이동, guide/target 좌표, 클릭/eraser 처리를 `MyApp` 내부 상태에 기록한다 (`src/main.py:646-1101`, `src/main.py:2570-2738`).
5. evaluation/screen/cryo/chemical 계획 UI에서 분주량과 위치를 구성한다 (`src/main.py:1122-1740`, `src/main.py:1804-1988`).
6. worksheet 생성 시 MxLIVE에서 당해 연도 experiment ID를 동기 호출로 조회하고, Echo/Shifter CSV 및 crystal metadata JSON을 각 마운트에 기록한다 (`src/main.py:1989-2414`, `src/utils/func.py:321-555`).
7. 업로드 버튼은 생성된 JSON을 순회하며 MxLIVE `/upload_labworks/BL-5C/`로 전송한다 (`src/main.py:2243-2256`). Shifter 완료 후 CSV 결과를 JSON에 병합해 재업로드하는 경로도 있다 (`src/main.py:2431-2451`, `src/utils/func.py:560-652`).

UI/비즈니스/데이터/연동은 물리적으로 일부 파일이 분리되어 있지만 `MyApp`이 이들을 직접 호출하고 내부 위젯 값을 비즈니스 입력으로 사용하므로 계층 분리는 매우 약하다.

## 3. 주요 품질·유지보수 문제

| 문제 | 근거와 사실/추정 | 심각도 | 작업량 | 회귀 위험 | 권장 검증 |
|---|---|---:|---:|---:|---|
| God class | **사실:** `MyApp`은 `src/main.py:90-2745`, 파일은 2,820줄·약 100개 함수. `initUI` 212줄, `makeEvalWorksheet0` 147줄 등 | High | Large | High | characterization test, workflow별 golden files, UI smoke test |
| UI thread에서 네트워크/대량 파일 스캔 | **사실:** 버튼 경로에서 `getMxlive.expOnMxlive()`와 filesystem scan을 동기 호출 (`src/main.py:521-566`, `2006`, `2345`) | High | Medium | Medium | 느린 fake server/mount로 UI 응답성 테스트 |
| 전역 환경 결합 | **사실:** import 시 `XTALVIEWER_PATH`, `USERHOME_PATH`, `PATHS` 결정 (`src/main.py:26-37`) | High | Medium | High | 환경별 configuration contract test |
| 중복 구현 | **사실:** 1,300~2,200줄대 진입점이 다수이며, `src/utils/utils`는 현재 utils의 복사본/구버전 혼합. MxLIVE get/put도 key·HTTP 코드 중복 | High | Large | High | 동작 스냅샷 후 한 경로씩 consolidation |
| 오류 은폐와 미정의 상태 | **사실:** `getMxlive.get_labworks()`는 오류를 `pass`한 뒤 정의되지 않을 수 있는 전역 `reply` 반환 (`src/utils/client/getMxlive.py:62-69`); `putMxlive.get_labworks()`도 동일 (`putMxlive.py:91-97`) | High | Small | Medium | timeout/4xx/5xx/invalid JSON 단위 테스트 |
| 입력 처리 버그 | **사실:** `processPlateIDs()`에서 단일 invalid 값은 정의되지 않은 `wrongs`에 append하며, 범위는 int 목록, 단일값은 str 목록이 된다 (`src/main.py:492-517`) | High | Small | Medium | `1,2-4,x`, 역범위, 빈 입력 테스트 |
| 불완전 예외 처리 | **사실:** `IndexError: pass`가 여러 곳이고 광범위 `Exception`을 print로만 처리. `misc.symlink_sf`는 bare except (`src/utils/misc.py:40-51`) | Medium | Medium | Medium | fault injection과 로그 assertion |
| 출력 파일 원자성/충돌 정책 부재 | **사실:** CSV/JSON을 직접 write하고 JSON은 같은 이름을 덮어씀 (`src/utils/misc.py:62-94`); worksheet만 번호 suffix 사용 | High | Medium | High | 중단·동시 실행·권한 실패 테스트 |
| 데이터가 로그로 노출 | **사실:** DB record 전체와 chemical 정보를 stdout에 출력 (`src/utils/func.py:480-553`, `src/main.py:2256`) | High | Small | Low | 민감필드 redaction 로그 테스트 |
| 사용자/project 매핑 하드코딩 | **사실:** 다수 계정명이 주석과 코드 매핑에 포함 (`src/utils/adpt.py:88-188`) | High | Medium | High | 운영 DB 매핑과 대조, unknown user 테스트 |
| 이름·상태 혼란 | **사실:** `evalue`, `crystname`, `xtalsToScreen`, `planType` 등 UI/도메인 이름 혼재; `selectImageType`, `targetSites`, `imageAdjustment`는 no-op (`src/main.py:424-429`) | Medium | Medium | Medium | 사용처·시그널 연결 inventory |
| 죽은/불완전 모듈 | **사실:** AST 기준 `comm.py`, `rank.py`, `rank_fx.py`와 중첩본은 SyntaxError. 활성 main은 이들을 import하지 않음 | Medium | Small | Low~High | 실제 배포 명령/사용자 인터뷰 후 격리 |
| 잘못된 root 사용자 대체 | **사실:** root 실행 시 username을 특정 사용자로 강제 (`src/utils/misc.py:118-123`) | Critical | Small | High | 서비스 계정 정책 확인, root 실행 거부 테스트 |

순환 import는 활성 경로에서 명백히 확인되지 않았다. 다만 `func → misc/adpt/dialogs`, `main → func/misc/...`의 강한 하향 결합과, 일부 legacy 모듈의 비패키지 import(`src/utils/genB.py:16`, `rank_fx.py:7`) 때문에 실행 디렉터리에 따라 import 결과가 달라질 수 있다.

## 4. 디렉터리·파일 정리 후보

### `src/utils/client/mxdc` 대 `src/utils/utils/client/mxdc`

- **사실:** 양쪽은 각각 295개 파일이며 상대 경로·SHA-256이 295/295 모두 동일하다. 단순 유사본이 아니라 현재 checkout에서는 byte-for-byte 완전 복제다.
- **사실:** 활성 `src/main.py`는 `utils.client.getMxlive/putMxlive`만 import하며 `mxdc` 패키지를 import하지 않는다 (`src/main.py:21-23`). top-level client는 필요한 signing/baseconv를 자체 보유한다.
- **추정:** 과거 MxLIVE client를 가져오는 과정에서 upstream MXDC 저장소 전체가 두 번 복사된 것으로 보인다. 단, 운영 배포 스크립트가 저장소 외부에서 이 경로를 직접 참조할 수 있어 즉시 삭제 확정은 불가하다.
- 권고: 외부 배포·라이선스·로컬 수정 여부를 확인한 뒤, 필요하면 upstream commit을 식별해 vendor 디렉터리 하나 또는 패키지 dependency로 격리한다. 불필요가 확인되면 두 트리 모두 제거 후보, 필요하면 중첩본만 우선 제거 후보이다.
- 분류: **High / Medium / 회귀 위험 Medium~High**. 검증: 운영 서버 `sys.path`, install script, crontab/systemd, shell history 대신 공식 배포 절차를 확인하고 clean environment import 및 기능 smoke test.

### `src/utils` 대 `src/utils/utils`

- **사실:** 공통 주요 파일 중 `chem.py`, `comm.py`, `genB.py`, `imgm.py`, `rank.py`, `rank_fx.py`, `smalltest.py`, `grid.txt`, `users.txt`는 동일하다. `adpt.py`, `func.py`, `misc.py`, `client/signing.py`는 서로 다르며 현재 바깥쪽 버전이 활성 main의 import 대상이다. 바깥쪽에만 `dialogs.py`와 최근 backup이 있다.
- **추정:** `src/utils/utils`는 예전 시점의 전체 복사본이고 현재 기능의 정본이 아니다. 그러나 파일별 차이가 있으므로 디렉터리 전체를 검토 없이 삭제하면 과거 동작/정보를 잃을 수 있다.
- 권고: Git 보존을 전제로 차이점을 ADR에 기록하고, 활성 경로 테스트 후 중첩 트리를 제거 검토. **High / Medium / 회귀 위험 Medium**.

### 삭제 가능성 분류

| 후보 | 판단 | 근거 | 위험/검증 |
|---|---|---|---|
| `*.pyc`, `__pycache__`, `*.swp` | 제거 강력 후보 | 생성물이며 3.6/3.7 캐시 포함; 일부는 현재 untracked/ignored | Low. clean clone과 import 검증 |
| `src/main.py_bk`, `main.py_241102`, `func.py_20241004` | 제거 후보 | Git이 이미 이력을 보존하고 활성 import 없음 | Medium. 현재 main과 diff를 기록하고 잃을 기능이 없는지 확인 |
| `main2.py`, `eval.py`, `evalue.py`, `sv2.py`, `sv3.py`, `pret.py`, `simply.py`, `evaSrc/` | 격리 후 제거 후보 | 실행 문서/호출 참조가 없고 상호 복제성이 높음 | High. 사용자별 실행 명령과 운영 shortcut 확인 필수 |
| `src/1`, `src/@`, `src/utils/@`, `src/temp*` | 제거 후보 | 비표준 이름의 코드 스냅샷/임시 파일이며 import 없음 | Medium. diff와 작성자 확인 |
| `src/new_main.py` | 보존 또는 별도 design note | 불완전하지만 미래 구조 의도를 담은 유일한 orchestrator 스케치 | Low. 소유자 의도 확인 |
| `data/BackdropImages.npz` | 조건부 보존 | legacy rank가 절대경로로 로드 (`src/utils/rank.py:172-176`) | High. ranking 기능 존속 여부 확인 |
| `chems/*.csv`, 화합물 PNG | 기본 보존 | 실제 screen library 입력일 가능성이 높고 main이 `PATHS['library']`를 탐색 (`src/main.py:1552-1570`, `1721-1736`) | Critical 데이터 위험. 데이터 소유자·버전·checksum 확인 |
| 두 `labwork.json` | 저장소에서 제거/비식별 fixture 교체 검토 | 실제 labwork schema와 사용자·protein·plate/harvest 필드를 포함 | High 보안/개인정보 위험. 값은 출력하지 말고 데이터 소유자 확인 |
| MXDC 두 트리 | 위 조건 충족 후 하나 또는 둘 제거 | 295개 파일 완전 중복, 활성 import 없음 | Medium~High. 외부 실행/라이선스 확인 |

## 5. 보안·운영 위험

| 발견 | 위치 | 판정 | 대응·검증 |
|---|---|---|---|
| TLS 검증 비활성화 및 경고 숨김 | `getMxlive.py:1-3,55-60`, `putMxlive.py:1-3,59-71`; 관련 client 사본들 | **Critical / Small / 회귀 Medium** | 신뢰 CA를 배포하고 `verify=True`; staging에서 인증서 chain/hostname/만료 테스트 |
| 1024-bit DSA private key 사용 | `getMxlive.py:21-41`, `putMxlive.py:26-46` | **Critical / Medium / 회귀 High** | 서버 프로토콜과 함께 현대 서명 방식·key rotation 설계; 상호운용 테스트 |
| 사용자 홈 외부의 key path | `_KEY_FILE` 계산은 `/data/users/<user>/.config/keys.dsa`가 됨 (`getMxlive.py:14-16`) | **High / Small / 회귀 High** | 실제 파일 권한(0600), 소유자, 의도 경로 확인 |
| 내부 주소/마운트 노출 | `src/main.py:27-35`, client `address` line 11 | **High / Medium / 회귀 Medium** | 환경 설정으로 이동, 저장소에는 예시만 유지, DNS/방화벽 matrix 테스트 |
| 실제 계정명·project mapping | `src/utils/adpt.py:88-188`, client의 주석 경로 | **High / Medium / 회귀 High** | 중앙 identity source로 이동, 과거 계정 노출 범위 검토 |
| 실험 메타데이터 fixture | 두 `src/utils/**/client/labwork.json` | **High / Small / 회귀 Low** | 값 비공개 상태로 데이터 소유자 판정 후 비식별 fixture 생성 |
| chemical library 및 plate 이미지 | `chems/*.csv`, `chems/UC-192873/*` | **Medium~High / Medium / 회귀 High** | 라이선스·민감도·보존정책 확인; public repo 여부 점검 |
| 민감 로그 | `src/utils/func.py:480-553`, `560-652`, `src/main.py:2256` | **High / Small / 회귀 Low** | 구조화 로그와 allowlist/redaction; stdout capture test |

저장소 정적 검색에서 평문 비밀번호·API token·PEM 인증서 자체는 확인되지 않았다. 이는 **현재 추적 파일 기준 사실**이며 Git 과거 이력은 initial import 한 commit뿐이다. 실제 `keys.dsa`는 저장소에 없지만 운영 경로와 생성 로직은 있다. 화학물질 CSV와 labwork JSON이 민감한지 여부는 기관 정책에 따른 **불확실성**이다.

## 6. 실행·배포 위험

- **macOS 대 Linux:** 현재 경로는 Linux 서버 마운트와 `/opt/python37/lib`를 전제한다. macOS에서는 `/smbmount`, `/data/users`, `LD_LIBRARY_PATH`가 통상 존재하지 않고 macOS는 동적 라이브러리 정책도 다르다. Apple Silicon에서는 구형 x86_64-only wheel이 특히 문제다. **High / Medium / 회귀 High**.
- **권한:** `/usr/local/XtalViewer`, `/data/users`, 공유 instrument mount에 쓰기 권한이 필요하다. `createFolder()`가 실패를 print만 하고 계속해 후속 파일이 일부만 생성될 수 있다 (`misc.py:8-13`). **High / Small / 회귀 Medium**.
- **경로/대소문자:** 문자열 결합을 광범위하게 사용하고 `RockMakerStorage`의 대소문자가 파일별로 다르다. macOS 기본 filesystem은 대소문자 비구분일 수 있지만 Linux는 구분한다 (`src/test2.py:4` 대 `main.py:29`). **High / Medium / 회귀 High**.
- **locale/encoding:** chemical CSV는 BOM이 있는 파일과 없는 파일이 섞여 있고 reader는 `utf-8-sig`를 사용해 일부 방어한다 (`misc.py:136-170`). worksheet CSV write에는 `newline=''`과 명시 encoding이 없어 OS별 blank line/기본 locale 차이가 가능하다 (`misc.py:62-75`). **Medium / Small / 회귀 Medium**.
- **GUI:** PyQt5/Qt platform plugin, display server(X11/Wayland), OpenGL/font가 필요하다. headless 운영 서버에서는 `DISPLAY`/xcb와 Qt plugin 문제 가능성이 높다. **High / Medium / 회귀 Medium**.
- **이미지:** OpenCV, NumPy, Pillow/RDKit은 native wheel/라이브러리에 의존한다. `cv2.imread` 실패가 `None`이어도 후속 처리하는 legacy scripts가 있다 (`src/test2.py:7-10`). **Medium / Medium / 회귀 Medium**.
- **네트워크:** timeout/retry/idempotency가 없다. URL path 자체에 사용자와 서명이 들어가 proxy/access log에 남을 수 있다 (`signing.py:37-40`, `getMxlive.py:44-56`). GUI freeze와 중복/부분 업로드 위험이 있다. **Critical / Medium~Large / 회귀 High**.
- **새 venv:** 기존 requirements를 그대로 설치하지 말고, 먼저 활성 runtime 최소 dependency와 운영 OS/CPU를 확정해 lock을 별도로 만들어야 한다. legacy MXDC/Amber 계열은 분리 환경이 필요하다. **High / Medium / 회귀 High**.

## 7. 테스트 가능성

### 현황

- **사실:** XtalFlow 자체의 pytest/unittest 기반 자동 테스트, test configuration, CI는 없다.
- `src/test.py`, `src/test2.py`, `src/utils/testground/test.py`는 절대경로 이미지와 GUI display를 사용하는 수동 실험 script이다.
- `src/utils/client/mxdc/tests/`는 vendored MXDC의 실험 코드이며 XtalFlow 회귀 테스트가 아니다. 중첩본도 동일하다.

### 리팩터링 전 최소 회귀 테스트

| 우선순위 | 테스트 | GUI 분리 가능성 | 근거/검증 |
|---|---|---|---|
| P0 | plate ID/range 정규화와 오류 입력 | 높음 | `main.py:492-517`; table-driven test |
| P0 | 2d/3d well→384 well 변환 전수 | 높음 | `adpt.py:6-84`; 192/288 mapping golden data |
| P0 | 최신 batch 및 image profile 탐색 | 높음 | `func.py:63-210`; 임시 directory tree fixture |
| P0 | evaluation/screen/chem worksheet CSV·JSON | 현재 낮음, 추출 후 높음 | `main.py:1804-2414`, `func.py:321-555`; 승인된 golden files와 장비 schema validator |
| P0 | MxLIVE 서명·조회·업로드의 성공/timeout/4xx/5xx | 높음 | client modules; fake HTTP server, 실제 값 없는 contract test |
| P0 | 부분 write/권한 실패/동시 실행 | 높음 | `misc.py:62-94`; fault injection |
| P1 | CSV BOM, 한글, newline round trip | 높음 | `misc.py:54-75,136-216` |
| P1 | plate/image 탐색 후 `plates_info` state | 중간 | `main.py:521-717`; Qt offscreen + fake service |
| P1 | 좌표↔pixel/µm 및 target add/remove | 중간 | `main.py:2570-2738`; pure geometry로 추출 전 characterization |
| P2 | 주요 버튼 연결과 탭 smoke | 낮음 | `main.py:102-313`; pytest-qt/offscreen 또는 Xvfb |

GUI 없이 바로 테스트 가능한 것은 `adpt`, 상당수 `misc`, `func`의 filesystem/CSV/JSON 함수, signing/client(HTTP mock)다. GUI에 강하게 결합된 것은 worksheet orchestration, plan validation, target state와 plate table이며 먼저 입력/출력 DTO를 정의해야 한다.

## 8. 불확실성과 추가 확인

1. 운영자가 실제로 `src/main.py`만 실행하는지, 과거 진입점별 shortcut이 남아 있는지.
2. `/smbmount/echo650`, `shifter1/2`가 파일 drop만으로 장비를 작동시키는지와 worksheet 공식 schema/원자적 전달 요구사항.
3. MxLIVE의 현재 API·서명 프로토콜, 1024-bit DSA 호환 제약, 신뢰할 CA chain.
4. `src/utils/client/mxdc`를 외부 배포나 다른 프로그램이 참조하는지 및 vendored upstream 버전/라이선스 의무.
5. chemical CSV, PNG, labwork JSON의 데이터 소유권·민감도·보존 정책.
6. 실제 운영 OS 배포판/CPU, Qt display 방식, SMB mount 옵션, 사용자/그룹 권한, locale.
7. 2d plate well 목록의 마지막 항목이 `H12d`인 것이 의도인지 (`src/utils/adpt.py:43-58`).
