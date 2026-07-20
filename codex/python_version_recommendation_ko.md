# XtalFlow Python 버전 및 의존성 권고

> 분석 기준일: 2026-07-20. 기존 파일은 수정하지 않았고, import 정적 분석과 공개 패키지 메타데이터를 비교했다.
> 외부 근거: [Python 버전 지원 상태](https://devguide.python.org/versions/), [PyQt5 PyPI](https://pypi.org/project/PyQt5/), [NumPy 1.19.5 PyPI](https://pypi.org/project/numpy/1.19.5/), [rdkit-pypi 2021.3.4 PyPI](https://pypi.org/project/rdkit-pypi/2021.3.4/).
> 경로 변경: 분석 후 레거시 `src/` 전체가 `src_xtalviewer_2_0_legacy/`로 이동되었다. 아래의 `src/...` 근거 경로는 모두 새 디렉터리 아래의 동일 상대 경로를 뜻한다.

## 결론

- **원래 기준 버전:** Python **3.7**이 가장 유력하다. **사실:** `src/run_program.sh:3`이 `/opt/python37/lib`를 지정하고, 저장소에 `cpython-36.pyc`와 `cpython-37.pyc`가 함께 있다. f-string과 `encoding=` 등을 사용하므로 Python 3 코드다. **추정:** 3.6에서 시작해 3.7 운영환경으로 이동한 뒤 일부 코드는 2024년까지 수정되었다.
- **현실적인 1차 마이그레이션 대상:** Python **3.11**.
- **장기 목표:** Python **3.13**. 단, 먼저 3.11에서 동작·schema를 고정하고 GUI/과학 패키지 최신 호환 조합을 검증한 뒤 올린다.
- **핵심 조건:** 현재 `requirements.txt`의 exact pin을 유지한 채 3.11로 갈 수는 없다. 활성 dependency만 다시 식별하고, NumPy/OpenCV/RDKit/PyQt5/cryptography 등을 호환 버전으로 함께 올리는 마이그레이션이다.

이 선택의 심각도/작업량은 **High / Large**, 회귀 위험은 **High**다. 검증은 Linux 운영 대상과 macOS 개발 대상 각각에서 clean venv 생성, import smoke, golden worksheet, image fixture, Qt offscreen smoke, staging MxLIVE contract test를 통과시키는 방식이어야 한다.

## 버전별 비교

2026-07-20 기준 Python 공식 상태는 3.8·3.9가 EOL, 3.10은 2026-10 EOL 예정, 3.11은 2027-10, 3.12는 2028-10, 3.13은 2029-10까지 지원 예정이다.

| 버전 | 현재 exact pins | 호환 패키지로 갱신 시 | 수정 필요성 | 유지보수 판단 |
|---|---|---|---|---|
| 3.8 | 구형 pins와 가장 가까움. NumPy 1.19.5·RDKit pin wheel 범위에 포함 | 가능 | Small~Medium | 이미 EOL. 재현용 임시 baseline 외 신규 운영 금지 |
| 3.9 | 현재 NumPy/RDKit pin이 공식 wheel을 제공한 마지막 범위 | 가능 | Small~Medium | 이미 EOL. “최소 변경 부팅” 진단용일 뿐 1차 목표로 부적절 |
| 3.10 | exact NumPy 1.19.5와 rdkit-pypi 2021.3.4 wheel 범위를 벗어남 | 넓게 가능 | Medium~Large | 2026-10 EOL로 투자 수명 부족 |
| 3.11 | exact scientific pins는 불가, 최신/근래 패키지 wheel 생태계는 성숙 | 매우 현실적 | Large | 1차 목표. 변화 폭과 생태계 안정성의 타협점 |
| 3.12 | exact pins 불가 | 현실적 | Large | 3.11보다 수명은 길지만 C-extension/API 변화 검증 폭이 큼. 1차 대안 |
| 3.13 | exact pins 불가 | 최신 패키지 선택 시 가능성이 높음 | Large | 장기 목표. legacy에서 한 번에 이동하면 문제 원인 분리가 어려움 |

**추정의 경계:** 애플리케이션 자체 문법은 3.11~3.13에서 대체로 파싱 가능하지만, 실제 위험은 Python 문법보다 native wheel, Qt plugin, OpenCV/RDKit 이미지 결과 차이, cryptography 서명 호환성이다. `src/utils/comm.py:21`, `rank_fx.py:44`, `rank.py:255`의 SyntaxError는 Python 버전 선택으로 해결되지 않는다.

## requirements와 실제 import 비교

### 활성 `src/main.py` 경로에서 직접/간접 확인된 외부 패키지

| dependency | 근거 | 현재 pin 평가 |
|---|---|---|
| PyQt5 | `src/main.py:6-12`, `dialogs.py:1` | `5.15.2`는 2020 pin. 최신 PyQt5는 Python 3.8+ abi3 wheel을 제공하지만 Qt5 장기 전략은 별도 결정 필요 |
| NumPy | `src/main.py:15` | `1.19.5`는 3.9 시대. 3.11+에서 pin 교체 필수 |
| OpenCV | `src/utils/func.py:8` | `4.5.3.56` 구형. 플랫폼 wheel과 영상 결과 regression 확인 필요 |
| RDKit | `src/utils/chem.py:7-9` | `rdkit-pypi==2021.3.4`는 Python 3.6~3.9 wheel만 명시. 현대 `rdkit` 배포로 전환 검토 |
| requests | client files line 1 | `2.14.2`는 매우 오래되고 TLS 동작 개선을 위해 갱신 필요 |
| msgpack | client files line 5 | `1.0.2`; 서명 key serialization compatibility fixture 필요 |
| cryptography | `src/utils/client/signing.py:4-7` | `2.3`은 매우 오래됨. 최신판에서 DSA/serialization 동작과 서버 계약 검증 필수 |

### legacy/비활성 후보에서만 확인된 패키지

`matplotlib`, Pillow, scikit-image, SciPy는 `eval*`, `sv*`, `rank.py`, `genB.py`, 수동 test에 나타나지만 현재 main의 정상 import graph에는 없다. 이 기능들이 실제 운영되는지 확인 후 optional extra 또는 별도 tool 환경으로 분리해야 한다. **Medium / Medium / 회귀 Medium**.

### 사용되지 않는 것으로 강하게 의심되는 top-level requirements

정적 import 기준 다음은 XtalFlow 소스에서 사용이 확인되지 않는다: `AmberLite`, `AmberUtils`, `MMPBSA.py`, `mpi4py`, `msgpack-numpy`, `networkx`, `packmol-memgen`, `ParmEd`, `pdb4amber`, `ply`, `psutil`, `pyMSMT`, `PySocks`, `pytraj`, `sander`, `tk` 및 여러 전이 dependency를 직접 pin한 항목(`asn1crypto`, `cffi`, `chardet`, `cycler`, `decorator`, `idna`, `kiwisolver`, `pycparser`, `pyparsing`, `python-dateutil`, `six`, `tifffile`, `urllib3` 등). 이는 **사실: import 미검출**, **추정: runtime에서 불필요**다. 동적 import나 외부 script 사용을 배제하려면 clean 환경 테스트가 필요하다.

특히 Amber/분자동역학 계열은 XtalFlow의 crystal image/worksheet 목적과 무관한 다른 환경의 `pip freeze`가 섞인 것으로 보인다. 일부는 pip 단독 설치가 어렵거나 AmberTools/Fortran/MPI 같은 시스템 stack을 요구할 수 있다. **High / Medium / 회귀 Low~Medium**. 권장 검증: 최소 dependency를 하나씩 설치한 clean venv에서 전체 workflow 실행.

### 누락 또는 잘못 표현된 dependency

- 활성 경로 기준 명백한 third-party 누락은 정적 분석에서 확인되지 않았다.
- `tk==0.1.0`은 표준 `tkinter`/OS Tk runtime을 뜻하지 않으며 현재 활성 코드가 Tk를 import하지도 않는다.
- vendored `mxdc/requirements.txt:1-25`는 PyGObject, Twisted, gepics, HDF5, Redis, python-magic 등 별도의 큰 GTK/beamline stack이다. top-level requirements와 합치면 안 된다.
- PyQt5는 pip wheel 외에도 Linux display/xcb/OpenGL/font 및 macOS platform plugin 검증이 필요하다. PyGObject/MXDC는 GLib/GTK/GObject introspection과 OS package manager가 필요할 가능성이 높다.

## 권장 dependency 재구성 절차

1. Python 3.7 운영환경이 살아 있다면 `pip freeze`, OS/CPU, Qt plugin, mount, 샘플 output checksum을 읽기 전용으로 보존한다. **High / Small / 회귀 Low**.
2. XtalFlow core와 vendored MXDC/legacy rank 도구를 분리한다. core 후보는 PyQt5, NumPy, OpenCV, RDKit, requests, msgpack, cryptography다. **High / Medium / 회귀 Medium**.
3. Python 3.11에서 각 패키지의 호환 범위를 넓게 선정한 뒤 lock file을 생성한다. exact version은 실험 결과로 결정하며 지금 문서에서 임의 고정하지 않는다. **High / Medium / 회귀 High**.
4. `requests`/`cryptography` 갱신은 TLS 검증 활성화와 서버 서명 계약 개선을 별도 workstream으로 취급한다. 단순 버전 bump와 보안 프로토콜 변경을 한 테스트에 섞지 않는다. **Critical / Medium / 회귀 High**.
5. Python 3.11 baseline이 안정된 뒤 3.13 CI를 추가하고, 이미지/worksheet 결과가 동일하거나 승인된 차이인지 확인한다. **Medium / Medium / 회귀 Medium**.

## 플랫폼별 설치 검증 matrix

| 대상 | 최소 검증 |
|---|---|
| Linux 운영 배포판/CPU | wheel 설치, Qt xcb/Wayland, headless 여부, SMB mount, CA bundle, file ownership/umask |
| macOS Intel | PyQt5/Qt, RDKit/OpenCV wheel, Finder launch 시 env 차이, `/smbmount` 대체 mapping |
| macOS Apple Silicon | arm64 wheel 존재, Rosetta 혼용 금지 여부, Qt/RDKit/OpenCV architecture 일치 |
| CI headless | `QT_QPA_PLATFORM=offscreen` 또는 Xvfb, filesystem fixtures, fake MxLIVE |

Python 3.8/3.9는 기존 환경을 재현해 golden output을 얻는 제한된 용도에는 유용하지만 인터넷/운영 시스템에 새로 배포해서는 안 된다. Python 3.12를 바로 1차 목표로 택하는 것도 가능하지만, 오래된 C-extension stack에서 문제를 한 단계 줄여 원인 분리를 쉽게 하려는 이유로 3.11을 우선한다.
