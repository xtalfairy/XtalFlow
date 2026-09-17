# XtalFlow

XtalFlow는 결정화 이미지를 검토하고 결정 타겟 좌표를 기록하며, 실험 계획과
SHIFTER/ECHO worksheet를 생성하는 프로그램입니다. 현재 지원하는 실험은 Raw Crystal과
Fragment Screening입니다.

## 사용 흐름

프로그램을 열면 시작 화면에서 실험 유형을 고르거나 `Recent work`의 실험을 이어서
엽니다. 실험은 다음 단계를 차례로 안내하며, 위쪽 단계 표시줄에서 언제든 다른 단계로
이동할 수 있습니다.

1. **Setup**: 단백질 이름과 실험 이름. 실험 ID는 확정할 때 단백질 이름으로 만듭니다.
2. **Select wells**: plate를 불러오고 이미지에서 결정을 클릭해 position을 찍습니다.
   position은 실험마다 따로 저장되고, well 경계 보정은 같은 workspace의 실험이
   함께 씁니다. `Review selected wells`로 경고가 있는 well을 확인합니다.
3. **Conditions**(Fragment Screening만): library, 사용할 행, well당 총량, 배정 순서.
   총량이 well 안의 position 수로 2.5 nL 단위 균등 분배되지 않으면 진행할 수 없습니다.
4. **Review**: 배정 표와 확인 목록을 보고 `Finalize and continue`로 revision을 확정합니다.
5. **Worksheets**: 설정된 모든 장비 폴더에 worksheet를 한꺼번에 저장합니다. 하나라도
   저장할 수 없으면 아무 파일도 저장하지 않고 다시 시도하거나 다른 폴더를 고르게
   합니다. MxLive 전송은 이 화면 아래에서 선택적으로 합니다.

Select wells의 `Show examples`는 `--examples-dir`로 지정한 폴더의 이미지를 보여줍니다.
각 이미지 옆에 같은 이름의 `.txt` 파일을 두면 설명으로 표시됩니다.

## 지원 Python 버전

- Python 3.9 이상 3.12 이하
- MxLive HTTPS 연결을 위해 표준 `ssl` 모듈이 포함된 Python이 필요합니다.
- 운영 서버에서는 Python 3.12.7로 설치를 검증했습니다.

가상환경을 만들기 전에 Python과 SSL을 확인합니다.

```bash
python3.12 -V
python3.12 -c 'import ssl; print(ssl.OPENSSL_VERSION)'
```

운영 서버의 Python이 `/usr/local/openssl`을 사용한다면 가상환경을 만들거나
활성화하기 전에 다음 환경변수를 설정합니다.

```bash
export LD_LIBRARY_PATH="/usr/local/openssl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

## 운영 서버 설치

다음 명령은 `pyproject.toml`과 `requirements.txt`가 있는 저장소 루트에서
실행해야 합니다. 다른 프로그램의 가상환경을 재사용하거나 복사하지 마세요.

```bash
cd /usr/local/fbdd_apps/XtalViewer/2.0

export LD_LIBRARY_PATH="/usr/local/openssl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

/opt/pyenv/versions/3.12.7/bin/python -m venv .venv3127
source .venv3127/bin/activate

python -V
python -c 'import ssl; print(ssl.OPENSSL_VERSION)'
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
```

`requirements-py39.txt`는 Python 3.9 운영 서버를 위한 호환 파일입니다.
Python 3.9~3.12에서 공통으로 사용할 수 있는 동일한 고정 버전을 설치합니다.
기존 XtalViewer의 의존성 목록은 `requirements-legacy.txt`에 보존되어 있습니다.
이 파일은 참고용이므로 XtalFlow 가상환경에는 설치하지 마세요.

설치 결과를 확인합니다.

```bash
which python
which xtalflow-viewer
which xtalflow-mxlive-read
python -c 'import xtalflow; print(xtalflow.__file__)'
```

공용 설치 환경에서는 root가 가상환경을 만들 수 있지만, XtalFlow는 각 연구자의
개인 계정으로 실행해야 합니다. 각 사용자는 저장소와 가상환경에 대한 읽기 및 실행
권한이 필요합니다. 사용자별 데이터베이스와 MxLive 인증정보를 공유하면 안 됩니다.

새로 로그인할 때마다 다음 환경을 활성화합니다.

```bash
export LD_LIBRARY_PATH="/usr/local/openssl/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
source /usr/local/fbdd_apps/XtalViewer/2.0/.venv3127/bin/activate
```

## Viewer 실행

개발 환경의 기본 설정은 저장소 내부 테스트 fixture를 가리킵니다. 운영 서버에서는
`DEFAULT_SETTINGS`를 `OPERATING_SERVER_SETTINGS`로 바꾸지 않았다면 다음과 같이
운영 경로를 명시해야 합니다.

```bash
xtalflow-viewer \
  --root /smbmount/rmserver/RockMakerStorage/WellImages \
  --library-dir /usr/local/fbdd_apps/XtalViewer/2.0/chems \
  --worksheet-dir /tmp/xtalflow/worksheets \
  --echo-dir /smbmount/echo650 \
  --shifter1-dir /smbmount/shifter1 \
  --shifter2-dir /smbmount/shifter2
```

### 장비 폴더 설정

worksheet를 받는 장비는 `/etc/xtalflow/xtalflow.toml`(또는 `--mxlive-config`로
지정한 파일)의 `[[instruments]]` 항목으로 설정합니다. 각 항목은 장비 ID, worksheet
종류(`echo` 또는 `shifter`), 출력 폴더, 표시 이름을 가집니다. 계획이 만드는 각
worksheet는 같은 종류의 모든 장비 폴더에 저장됩니다. 예를 들어 SHIFTER를 한 대 더
연결하려면 `worksheet = "shifter"` 항목을 추가합니다. 예시는
[`xtalflow.example.toml`](./xtalflow.example.toml)에 있습니다.

`[[instruments]]`가 없으면 ECHO 650, SHIFTER 1, SHIFTER 2 기본 구성을 사용합니다.
`--echo-dir`, `--shifter1-dir`, `--shifter2-dir`는 설정된 `echo650`, `shifter1`,
`shifter2` 장비의 폴더만 바꿉니다.

ECHO/SHIFTER 경로가 저장소 밖에 있으면 XtalFlow은 해당 폴더가 실제로 마운트된
네트워크 공유(cifs, smb3, nfs)인지 확인합니다. 공유가 마운트되지 않아 빈
mountpoint만 남아 있으면 worksheet를 로컬 디스크에 쓰지 않고 저장 위치를 다시
선택하도록 안내합니다. 테스트 목적으로 로컬 폴더에 저장해야 할 때만
`--allow-local-instrument-dirs`를 추가합니다. 이 경우 장비는 파일을 받지 못합니다.

전체 실행 옵션은 다음 명령으로 확인할 수 있습니다.

```bash
xtalflow-viewer --help
```

## MxLive 읽기 전용 검증

운영 서버에서 검증된 MxLive hostname은 `mxlive.postech.ac.kr`입니다.
기존 주소인 `mxlive.5c.postech.ac.kr`은 서버 인증서의 hostname과 일치하지 않습니다.

### MxLive 계정 매핑 설정

XtalFlow은 기본적으로 OS 사용자명, MxLive 사용자명, MxLive API의
`project_id`(실질적으로 계정 ID)가 같다고 간주합니다. 서로 다른 환경에서는
[`xtalflow.example.toml`](./xtalflow.example.toml)을 복사하여 운영 서버의
`/etc/xtalflow/xtalflow.toml`에 두고 사용자별 매핑을 설정합니다.

```toml
[mxlive]
base_url = "https://mxlive.postech.ac.kr"
beamline = "BL-5C"
ca_bundle = "/etc/pki/tls/certs/ca-bundle.crt"

[mxlive.accounts.local_user]
username = "remote_mxlive_user"
account_id = "remote_mxlive_id"
key_path = "/data/users/local_user/.config/mxdc/keys.dsa"
```

개인키 내용은 설정 파일에 넣지 않고 경로만 기록합니다. `root` 계정은 명시적인
매핑이 없으면 WebDB 미리보기만 가능하며 실제 업로드 준비 상태가 되지 않습니다.
다른 위치의 설정은 뷰어 실행 시 `--mxlive-config`로 지정할 수 있습니다.

### 검토 데이터베이스 업그레이드

새 XtalFlow가 이전 버전의 검토 데이터베이스를 처음 열면, 스키마를 바꾸기 전에 같은
폴더에 백업을 만듭니다. 파일 이름은 `reviews.sqlite3.schema<이전 버전>-<UTC 시각>.bak`
형식이며 상태 표시줄에 위치가 표시됩니다. 업그레이드는 하나의 트랜잭션으로 실행되므로
중간에 실패하면 데이터베이스는 원래 상태로 남습니다.

더 최신 XtalFlow가 업그레이드한 데이터베이스는 이전 버전에서 열지 않습니다. 이 경우
설치된 XtalFlow를 최신 버전으로 맞춥니다.

스키마 18부터 position은 실험마다 따로 저장됩니다. 업그레이드할 때 확정된 실험이 쓰던
position은 그 실험으로 복사되고, 어느 실험에도 쓰이지 않은 position은 workspace에
남아 새 실험을 시작할 때 가져올지 묻습니다. 이전 버전으로 되돌려야 하면 업그레이드 때
만든 `reviews.sqlite3.schema17-*.bak` 백업을 복원합니다.

### 사용자별 이미지 검토 설정

이미지 아래의 자동 well 확정 신뢰도는 사용자별로 다음 파일에 저장됩니다.

```text
~/.config/xtalflow/preferences.json
```

`Auto-confirm this plate`는 plate를 불러올 때 기본으로 활성화됩니다. 저장된
신뢰도 이상인 자동 검출 결과만 확정합니다. 사용자가 특정 plate에서 이 설정을
끄면 같은 실행 세션 동안에는 plate를 전환해도 꺼진 상태를 유지합니다.

MxLive v2는 서명된 username과 해당 사용자의 레거시 DSA key를 이용해
인증합니다. 사용자별 key 위치는 다음과 같습니다.

```text
/data/users/{username}/.config/mxdc/keys.dsa
```

실제 MxLive 사용자 계정으로 실행하거나, 서로 일치하는 username과 key를 함께
명시합니다.

```bash
xtalflow-mxlive-read \
  --url https://mxlive.postech.ac.kr \
  --beamline BL-5C \
  --username "$USER" \
  --key "/data/users/$USER/.config/mxdc/keys.dsa" \
  --ca /etc/pki/tls/certs/ca-bundle.crt \
  --year 2026
```

sample 내용을 출력하지 않고 sample API와 전체 개수만 확인하려면 다음과 같이
실행합니다.

```bash
xtalflow-mxlive-read \
  --url https://mxlive.postech.ac.kr \
  --beamline BL-5C \
  --username "$USER" \
  --key "/data/users/$USER/.config/mxdc/keys.dsa" \
  --ca /etc/pki/tls/certs/ca-bundle.crt \
  --year 2026 \
  --include-sample-count
```

이 명령은 GET 요청만 수행합니다. MxLive record를 생성하거나 변경하지 않습니다.

## 자주 발생하는 설치 오류

### `No matching distribution found for requirements.txt`

requirements 파일을 설치할 때는 `-r` 옵션이 필요합니다.

```bash
python -m pip install -r requirements.txt
```

### `Cannot import 'setuptools.build_meta'`

editable package보다 requirements를 먼저 설치해야 합니다.
`setuptools==75.8.2`는 `requirements.txt`에 포함되어 있습니다.

```bash
python -m pip install -r requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
```

### `xtalflow-viewer: command not found`

XtalFlow 가상환경을 활성화하고 editable package를 설치합니다.

```bash
source .venv3127/bin/activate
python -m pip install --no-deps --no-build-isolation -e .
```

### `Python SSL support is unavailable`

Python이 정상적인 `_ssl` 모듈 없이 빌드되었거나 OpenSSL runtime library를 찾지
못하는 상태입니다. MxLive에 접속하기 전에 Python/OpenSSL 설치를 수정해야 합니다.
XtalFlow는 TLS 검증을 비활성화하지 않습니다.

### MxLive hostname 불일치

`https://mxlive.postech.ac.kr`을 사용하세요. 다른 CA bundle을 지정하더라도
인증서의 hostname 불일치는 해결되지 않습니다.

## 개발 환경 검증

```bash
source .venv_air/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
```
