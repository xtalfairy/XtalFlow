# 조건 행렬 실험 설계 — Solvent / Cryo Test를 실험 노트처럼 계획하기

작성일: 2026-09-17  
상태: 1차 구현 완료 (`experiment-first` 브랜치, 11절 참고)  
관련 문서: `codex/astra_experiment_first_workflow_ko.md`

## 1. 목적

결정을 본 실험(Fragment Screening)에 쓰기 전에 "이 결정이 어떤 용매·cryoprotectant를 어느 농도에서 얼마나 오래 견디는가"를 확인하는 실험을 계획한다. 사용자는 실험 노트에 표를 그리듯 조건을 적고, 결정을 아끼기 위해 일부 조건을 지우고, 필요한 결정 수만큼 well을 고른다. XtalFlow는 최종 농도를 nL로 바꾸고, 분주·수확 순서와 worksheet를 만든다.

사용자 결정(이미 확정):

- 입력은 **최종 농도(%)**, nL은 계산해서 함께 보여준다.
- **순서 있는 복합 처리**를 지원한다(용매 → 대기 → cryo → 수확).
- well 배정 기본값은 **조건별 묶음**.
- 조건 줄이기는 **입력 표 안에서** 셀을 흐리게 제외한다. 별도의 "내 계획/취소" 영역은 두지 않는다.
- 결과 기록(결정 상태, 해상도, heatmap)은 **다음 단계**로 미룬다. 이번 설계는 조건 ID를 안정적으로 유지해 결과를 나중에 붙일 수 있게만 한다.

## 2. 레거시에서 확인한 사실

`src_xtalviewer_2_0_legacy/`에는 Evaluation/PreTest 기능이 네 가지 판으로 남아 있고 어느 것도 완결되지 않았다.

| 항목 | 레거시 | 새 설계에 주는 의미 |
|---|---|---|
| 실험 종류 | 독립된 Solvent/Cryo 유형 없음. 모두 `PreTest` | 화면 카드는 둘로, 저장 유형은 하나로 |
| 실험 ID | `PreTest-YYYYMM-PROTEIN-NN`, MxLive `expri_id` 기준 번호 | 같은 형식 유지 |
| 조건 표현 | nL 값만(`SV10nl`, `DMSO15nl`). 농도·drop 부피 계산 없음 | 새로 모델링. drop 부피 출처 확인 필요 |
| 시간 | 라벨뿐(`0, 1h, 2h`). 타이머·일정 없음, 분/시간 혼용 버그 | 분 단위 정수로 저장, 표시만 변환 |
| 반복 | 3 고정(v1) 또는 입력 | 셀별 반복 수 |
| 대조군 | 없음(0h가 유일한 기준) | 0% 대조군 기본 포함 |
| ECHO | 판마다 헤더가 다름. v2/v3는 앞에 `Harvest Time` 열 추가, 추가제별 파일 | 표준 8열 유지 + 별도 일정표(확인 필요) |
| SHIFTER | 15열, 시간·주석 미기록. 수확 후 파일을 다시 읽어 `TimeDeparture`·`Comment` 사용 | 헤더·열 위치 유지, 쓰는 열은 확인 필요 |
| MxLive | `soak_plate='pretest'`, `soak_well='Z00'`, `soak_id`에 조건 라벨, DMSO/EG SMILES 고정 | 표식 유지, 조건 라벨 형식 정리 |
| subwell `d` 보정 | Evaluation은 x, Screen은 y에 −700 µm | 새 코드의 `plate_format.echo_offset_um` 하나로 통일 |

레거시 버그(cryo만 있는 조건은 아무것도 만들지 않음, 같은 추가제 두 번 쓰면 부피 소실, 클릭마다 새 ID 등)는 옮기지 않는다.

## 3. 실험 유형과 단계

- 시작 화면 카드: **Solvent / Duration Test**, **Cryo Test**. 둘 다 같은 유형 `condition_test`(ID 접두어 `PreTest`)를 만들고 템플릿만 다르다.
  - Solvent / Duration Test 템플릿: 탭 `DMSO`, 열 `0, 5, 10, 20 %`, 행 `0, 30 min, 1 h, 2 h`, 반복 2.
  - Cryo Test 템플릿: 탭 `Glycerol`, 열 `0, 10, 20, 25 %`, 행 `0 min`(즉시 수확), 반복 2.
- 단계: **Setup → Conditions → Select wells → Review → Worksheets**. Fragment Screening과 달리 조건을 먼저 정해야 필요한 결정 수를 알 수 있으므로 Conditions가 Select wells 앞에 온다.

## 4. 데이터 모델 (`domain/condition_matrix.py`)

```text
Additive        id, name, stock_percent(기본 100), smiles(선택), source_plate, source_wells[]
TreatmentStep   kind = add | wait,  additive_id, final_percent | minutes
Condition       id(생성 시 UUID, 이후 불변), steps[], replicates, excluded, note
ConditionTest   additives[], conditions[], drop_volume_nl, purpose, notes
```

- **조건 = 처리 단계 목록**이다. 행렬은 조건을 만드는 생성기일 뿐 저장 대상이 아니다.
  - 탭(추가제) × 열(최종 %) × 행(시간)으로 `[add X to c%, wait t, harvest]` 조건을 만든다.
  - 복합 처리 탭은 "앞 단계(고정)"를 가진다. 예: 앞 단계 `add DMSO 10 %, wait 1 h` + 행렬 변수 `add Glycerol c %, wait t`.
- 행렬 칸을 다시 만들 때 같은 (탭, %, 시간) 칸은 기존 조건 ID·제외·반복·메모를 이어받는다. 결과 기록이 조건 ID에 붙기 때문이다.
- 초안 저장: `planning_draft`에 `conditions_json` 열 추가(스키마 19, 기존 migration 규칙과 백업 동일). 확정 revision snapshot에는 조건·계산된 nL·배정을 모두 넣어 이후 설정이 바뀌어도 재출력이 같게 한다.

## 5. 농도 → nL 계산

drop 부피 `V`, 그 추가제의 현재 양 `a`(nL 환산), stock 농도 `S`, 목표 최종 농도 `c`일 때

```text
(a + S·Va) / (V + Va) = c   →   Va = (c·V − a) / (S − c)
```

- 순서 있는 처리에서는 앞 단계에서 늘어난 부피를 다음 단계의 `V`로 쓴다.
- `Va`는 2.5 nL 단위로 반올림하고, 실제 %를 옆에 표시한다(`10 % → 22.5 nL · 실제 9.9 %`).
- well 안 position 수로 2.5 nL 균등 분배가 안 되면 Fragment Screening과 같은 규칙으로 막고, 가능한 가장 가까운 농도 둘을 제안한다.
- `c ≥ S`, 음수 부피(이미 목표보다 진함)는 그 칸에 오류로 표시한다.
- "최종 농도"는 **그 단계 직후의 농도**로 정의한다. 뒤 단계의 희석 때문에 수확 시점 농도가 달라지면 Review에 수확 시점 농도를 함께 보여준다.

## 6. Conditions 화면 — 실험 노트형 입력

```text
[DMSO ×] [Glycerol ×] [+ Add additive]        Stock 100 %  Source LDV-01 · A1, A2

            0 %        5 %        10 %       20 %        ⋯
0 min     ▣ 2 ctrl   ▣ 2        ▣ 2        ▣ 2
30 min    ▣ 2        ▣ 2        ░ excluded ▣ 2
1 h       ▣ 2        ▣ 2        ▣ 2        ▣ 2
2 h       ▣ 2        ░ excluded ▣ 2        ▣ 2
+ time                                                 + concentration

Crystals: 28 needed · you have [ 30 ]  ✓ 2 spare
```

- 셀: 클릭으로 포함/제외 토글, 숫자는 반복 수(휠·키보드로 변경). 제외 셀은 지우지 않고 흐리게 남긴다(실험 노트의 줄긋기).
- 행·열 머리글 `⋯`: 전체 제외/포함, 반복 수 일괄 변경, 메모.
- 0 % 열은 대조군으로 기본 포함. 남은 조건이 쓰는 대조군을 끄면 경고만 한다(막지 않음).
- **결정 예산**: "사용 가능한 결정 수"를 입력하면 필요 수·여유를 보여주고, 부족하면 "반복 2 → 1로 줄이면 16개" 같은 제안 버튼을 준다.
- 같은 데이터를 **조건 목록 보기**로 전환할 수 있다(복합 처리의 단계 순서 확인용). 상태는 하나이며 어느 보기에서 고쳐도 같다.
- 노트 요소: 목적, 실험 메모, 조건별 메모. 작성자·날짜는 자동. `Save as template…` / `Start from previous experiment…`.

## 7. Select wells와 배정

- 선택 막대에 `24 / 28 crystals` 진행률.
- 기본 배정은 **조건별 묶음**: 클릭(선택) 순서대로 조건 1의 반복 1..n, 조건 2 … 순. 한 plate의 이웃 well이 같은 조건이 되어 수확이 편하다.
- Conditions 표의 각 셀에 배정된 well 주소를 작게 표시하고, Review에서 두 well의 조건을 맞바꿀 수 있다.
- 결정이 모자라면 뒤쪽 조건이 비어 있음을 Review에서 명시하고 확정을 막는다(몰래 빠진 조건 없음).

## 8. 일정과 출력

수확 시각 기준(**확인 필요**, 9절 Q2)에 따라 일정표를 만든다. 권장안 A 기준:

```text
T+0      ECHO round 1   DMSO 모든 조건            PreTest-…-01_R1.csv
T+0      Harvest        0 min 조건 8 wells         (SHIFTER 순서 1–8)
T+30 min Harvest        30 min 조건 6 wells
T+1 h    ECHO round 2   Glycerol(복합 2단계)       PreTest-…-01_R2.csv
…
```

- **ECHO**: 현재 표준 8열 헤더 그대로, 같은 시각에 분주하는 단계끼리 한 파일(`_R1`, `_R2` …). 레거시 v2/v3의 `Harvest Time` 열은 넣지 않는 것을 권장(Q3).
- **SHIFTER**: 기존 15열, 한 파일. 행 순서 = 수확 시각 순. 파일 이름은 실험 ID로 시작(레거시가 접두어로 수확 결과 파일을 찾음).
- **수확 일정 체크리스트**: Worksheets 화면에 시각·작업·well 목록, `Copy` / `Save as CSV`.
- **MxLive labwork**: `soak_plate='pretest'`, `soak_well='Z00'`, `soak_id = "{조건 라벨}-r{반복}"`(예: `DMSO10%-1h-r2`, 복합은 `DMSO10%-1h+Gly20%-0min-r1`), `soak_smile`은 추가제 SMILES(DMSO·EG·glycerol 기본 제공, 그 외 사용자 입력, 없으면 `none`), `soak_vol`은 그 well의 총 분주 nL.
- 원자적 저장, 실패 시 복구, 확정 revision 기준 출력은 기존 Worksheets 규칙을 그대로 쓴다.

## 9. 구현 전에 확인할 것

| # | 질문 | 권장 기본값 |
|---|---|---|
| Q1 | **drop 부피**는 어디서 오나? plate 종류별 고정값인가, 실험마다 다른가? | Setup에서 입력(plate 종류별 기본값을 설정 파일에서 제안) |
| Q2 | **수확 시각 기준**: A) 모두 T+0에 분주하고 시간별로 수확 B) 긴 조건부터 늦춰 분주해 한꺼번에 수확 | A (ECHO 한 번, 수확이 여러 번) |
| Q3 | ECHO가 표준 8열 외 `Harvest Time` 같은 추가 열을 허용하나? | 추가 열 없이 round별 파일 |
| Q4 | SHIFTER `Comment`(수확 후 `harvest_comment`로 읽힘)나 `ExternalComment`에 조건 라벨을 써도 되나? | 비워 둠(레거시와 동일) |
| Q5 | 추가제 source plate의 **well당 사용 가능 부피**(레거시 상수 28000 nL은 근거 불명) | 설정 파일 값, 초과 시 다음 source well 사용 안내 |
| Q6 | MxLive에서 `pretest`/`Z00` 표식을 계속 써야 하나? | 유지 |
| Q7 | 기본 템플릿의 농도·시간·반복 값 | 3절의 값 |

## 10. 구현 순서 (확인 후)

1. `domain/condition_matrix.py`: 모델, 행렬 생성·ID 유지, 농도 계산, 필요 결정 수 — 단위 테스트 먼저.
2. 스키마 19(`conditions_json`)와 snapshot 직렬화·역직렬화(바이트 동일 왕복 테스트).
3. `ui/condition_matrix_editor.py`: 탭·표·셀 토글·머리글 메뉴·예산.
4. 단계 순서(Conditions → Select wells), 조건별 묶음 배정, Review 표.
5. 일정 계산, ECHO round 파일, SHIFTER 순서, 체크리스트, MxLive 레코드.
6. 시작 화면 카드 활성화, README·제안서 갱신.

## 11. 1차 구현 현황 (2026-09-17)

9절 질문은 아직 답을 받지 못해 권장 기본값으로 구현했고, 확인되지 않은 수치는 기본값으로 고정하지 않았다.

| 항목 | 구현 |
|---|---|
| 실험 종류 | 왼쪽 패널 New experiment에 **Solvent Test**(DMSO 0/5/10/20 % × 0/30 min/1 h/2 h, 반복 2)와 **Cryo Test**(Glycerol 0/10/20/25 % × 0 min, 반복 2). 저장 유형은 `condition_test` 하나, 실험 ID `PreTest-YYYYMM-PROTEIN-NN` |
| 단계 | Setup → Conditions → Select wells → Review → Worksheets |
| Q1 drop 부피 | Setup의 필수 입력(기본값 없음) |
| Q2 수확 기준 | A안: T+0 분주, 조건별 시간에 수확. 복합 처리의 뒤 단계는 앞 단계 대기 후 따로 분주 |
| Q3 ECHO | 표준 8열 그대로, 분주 시각별 파일 `_R1`, `_R2`(한 번뿐이면 접미사 없음) |
| Q4 SHIFTER | 15열, Comment 비움, 수확 시각 순 |
| Q5 source 용량 | 검사하지 않음. Review에 추가제별 필요 총량 표시 |
| Q6 MxLive | `soak_plate=pretest`, `soak_well=Z00`, `soak_id=DMSO10%-1h-r2` 형식 |
| 조건 입력 | 탭(추가제)마다 stock %, source plate/well, SMILES, 선택적 앞 처리(After … at … for …). 표 셀 클릭으로 포함/제외(지우지 않고 줄긋기), 우클릭으로 반복 수, 머리글 우클릭으로 행·열 일괄 변경·삭제. 열 머리글에 분주 nL와 실제 % |
| 결정 예산 | Crystals available 입력 시 필요/부족 수와 "반복 1로 줄이기" 제안 |
| 저장 | 스키마 20: `planning_draft.details_json`, worksheet 출력 키를 순서 기준으로 변경(한 장비에 여러 파일) |

다음 단계 후보: 결과 기록(결정 상태·해상도 heatmap), 조건 목록 보기, 템플릿 저장/불러오기, source well 용량 설정.
