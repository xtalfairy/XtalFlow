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

## 12. Conditions 화면 2차 정리 (2026-09-17)

| 항목 | 변경 |
|---|---|
| 상단 | 제목 `Compare solvent concentration and soaking time`(Cryo는 cryoprotectant), 한 줄 설명. Solvent · Stock concentration · Initial drop volume을 한 줄에. drop 부피는 Setup과 같은 값(`design.drop_volume_nl`) |
| Dispensing source | 별도 작은 영역. 빈 plate/well 칸은 주황 테두리와 "Enter the source …; the ECHO worksheet needs it." |
| 시간 표기 | `30`, `30 min`, `30 minutes`, `1h`, `1 hr`, `1 hour`, `1.5 h`, `1 h 30 min`을 모두 읽는다. 분 단위로 떨어지지 않는 값(`1.51 h`)과 읽을 수 없는 값(`soon`, `30 s`)만 거절하고, 오류 문구에 쓸 수 있는 형식을 같이 보여 준다 |
| 축 | 표 머리글에서 직접 편집. 가로·세로 머리글 끝에 `+` 머리글을 가진 좁은 빈 열·행이 있고, 누르면 그 자리에 입력칸이 열려 값 하나나 여러 개(`30, 40`)를 받는다. 머리글 더블클릭(또는 우클릭 → Edit value…)은 그 값을 고치며 조건 ID와 칸별 설정을 유지, 머리글에 마우스를 올리면 나타나는 × 로 그 열·행을 지운다. 읽을 수 없는 값은 입력칸이 빨갛게 남고 표 아래에 이유와 쓸 수 있는 형식을 보여 주며 설계는 바뀌지 않는다. `Soaking duration`은 표 왼쪽 위 모서리에, `Final concentration in drop`은 우측 상자의 Requested·Actual 위에 적는다. `+` 칸은 선택·방향키 대상이 아니다 |
| 셀 | `2 wells` / `Excluded`. 클릭·방향키는 선택만, Space·Enter 포함/제외, +/− well 수. 포함/제외 버튼(× / +)은 선택된 칸 안에만 하나 나타난다(칸마다 체크박스를 두지 않음). 우클릭 메뉴는 보조 |
| 선택 조건 패널 | 그 칸(=그 well)에 해당하는 것만 보여 준다: 제목(조건 이름), Wells, 제외 상태, Requested / Transfer / well / Actual(`compute_doses` 결과 그대로), Planned harvest. 농도·시간은 열·행 전체의 값이라 여기서 고치지 않는다(행렬 모델상 한 칸만 이웃과 다른 값을 가질 수 없음) |
| Crystal drops per condition | 표 전체의 기본값(`design.default_replicates`), 표 바로 위. 우측 상자는 표 윗선에서 시작 |
| Wells per condition | `design.default_replicates`. 바꾸면 이전 기본값을 쓰던 조건만 따라가고 따로 정한 조건은 유지 |
| 0 % 이름 | `No DMSO added`, 앞 처리가 있으면 `No additional Glycerol`. "Control"이라 부르지 않음(진짜 대조군 정의는 실험자 결정) |
| 앞 처리·SMILES·추가 solvent | `Advanced` 접힘 영역으로 이동, 팝업(AdditiveDialog) 제거. 앞 처리 시간은 검증 후에만 적용 |
| solvent 칩 | 한 종만 있어도 탭처럼 칩이 보이고, 그 오른쪽에 `+ Add solvent`. 칩마다 작은 × 로 그 solvent를 지운다(마지막 것만이 아니라 아무거나, 마지막 하나는 지울 수 없음). 조건이 있으면 몇 개 조건·well이 사라지는지 묻고 지운다. 열려 있던 solvent는 그대로 열려 있다 |
| 하단 | 상태 `16 conditions · 32 wells required`, 옆에 실제 선택 `Selected 20 wells · 2 will remain unused` / `… more needed` / `No wells selected yet`. 버튼 `Select 32 wells`, `Select 5 more wells`, 충분하면 `Review assignments`(Review로 이동) |
| 제거 | 미리보기 없이 모든 반복을 1로 바꾸던 "Use 1 crystal each" 버튼. 표 아래 분주량 요약 줄과 Crystals on hand 입력칸(우측 상자·하단 요약과 숫자가 겹쳐서). `available_crystals` 값은 데이터에만 남는다 |
| 일정 표현 | Review 탭 `Planned schedule`, Worksheets `Planned schedule · relative to the start of dispensing (T+0)`. Review의 순서 선택은 `Assign wells in`(재배정임을 도움말로 명시) |
| JSON | `default_replicates`, `kind`는 기본값이 아닐 때만 기록해 기존 확정본 snapshot이 바이트 단위로 같게 유지 |

미결정: 허용 오차/장비 최소 부피(현재 2.5 nL 단위 반올림만 있음), 진짜 대조군 정의, 0 min의 실행 의미와 템플릿의 0 min 행 유지 여부, soaking 시작 기준(현재 전체 첫 분주 시작 = T+0, well별 분주 시각은 반영하지 않음).

## 13. Dispensing source: plate 종류, dead volume, 준비할 well (2026-09-18)

Dispensing source 상자는 Conditions 화면 맨 아래에 있다. 위의 조건이 정해져야 source에 무엇을 얼마나 넣을지 정해지기 때문이다.

| plate | calibration | 작업 범위 µL (하한 = dead volume) |
|---|---|---|
| 384PP (Beckman 001-14555, 이전 PP-0200) | 384PP_DMSO2 | 15–65 |
| 384PP | 384PP_AQ_GP3 (buffer / protein: surfactant 없는 시약, 물·buffer·glycerol 보관 용액의 단백질) | 15–65 |
| 384PP | 384PP_AQ_SP2 (surfactant / protein: surfactant가 든 buffer) | 15–65 |
| 384PP | 384PP_AQ_CP (crystallization reagents: 염, MPD, PEG, 삼투압 용액) | 20–50 |
| 1536LDV (Beckman 001-6969, 이전 LP-0400) | 1536LDV_DMSO | 1–5.5 |

출처: HKU CPOS Echo650 SOP Table 1, Beckman Echo Qualified Consumables 브로셔(384PP 20–65 µL, 1536LDV 1–5.5 µL, "fluid class에 따라 조금 다를 수 있음"). 1536LDV의 수용액·glycerol 범위는 찾지 못해 DMSO만 제공한다. 선택 목록 이름에는 protein, crystallization reagents를 줄이지 않고 적는다(사용자 요청).

- 한 well이 줄 수 있는 양 = (최대 − dead volume) / 1.1. 채울 양 = dead volume + 줄 양 × 1.1(10 % 여유), 0.1 µL 단위 올림, 최대를 넘지 않음. 여유분은 안내 문구에 항상 적는다.
- transfer는 순서대로 첫 well부터 뽑고, 다음 transfer가 dead volume 아래로 내려가게 하면 다음 well로 넘어간다. 한 transfer는 나누지 않으며, 한 dose를 position에 나눈 행은 같은 well에서 뽑는다.
- well이 부족하면 "DMSO needs 2 source wells; 1 entered. Add 1 more, e.g. A2."처럼 추가할 주소를 제안하고 진행을 막는다. 필요 이상 입력한 well은 "not needed"로 알린다. plate에 없는 주소(384: A1–P24, 1536: A1–AF48), 다른 첨가제와 같은 well은 거절한다.
- 새 실험은 plate 종류가 필수. plate 종류가 없는 이전 설계·확정본은 예전처럼 한 well, 부피 검사 없음이며 저장 형식과 worksheet가 바이트 단위로 같다.
- Review의 Source use, Worksheets 단계의 "Prepare the source plate before dispensing"에 well별 채울 양을 보여 준다.

## 14. 공용 source plate 사용 기록 (2026-09-18)

ECHO 옆 공용 solvent test plate를 여러 사용자가 계정과 관계없이 같이 쓴다. well은 재사용하지 않는다.

- 기록 위치: 모든 사용자가 worksheet를 저장하는 ECHO 폴더의 `source-plates/<plate 이름>.log`. plate 이름은 대소문자를 구분하지 않는다. 한 줄에 JSON 하나로 시각·plate·종류(used / skipped)·well·사용자·실험 ID·첨가제·이유를 적고, 덧붙이기만 한다. 네트워크 공유에서 잠금이 불안정한 SQLite는 쓰지 않는다.
- well 순서: 열 방향(A1→P1, A2…; 1536은 A1→AF1, A2…). 다음 well은 마지막으로 **사용된** well 다음부터이고, 기록된 well(사용·건너뜀)과 같은 설계의 다른 첨가제 well은 건너뛴다. 중간 빈 칸으로 되돌아가지 않는다.
- Conditions: plate 이름과 종류를 정하면 필요한 수만큼 다음 빈 well이 자동으로 들어간다(plate나 종류를 바꾸면 다시 고름). `Use next wells`로 다시 고를 수 있고, 기록된 well을 직접 입력하면 누가 언제 썼는지 알리고 받지 않는다.
- `Skip a well`: well과 이유(Dust or debris, Damaged, Used outside XtalFlow, Other)를 골라 즉시 공용 기록에 남긴다. 쓰려던 well이면 다음 빈 well로 바뀐다. 기록을 쓸 수 없으면 이 실험에 보관했다가 worksheet 저장 때 함께 기록한다(앱을 닫으면 사라짐). 되돌리기는 없다.
- worksheet 저장: plate별 잠금 파일(배타적 생성, 120초 넘은 잠금은 비정상 종료로 보고 제거)을 잡고 기록을 다시 읽어, 그사이 다른 사람이 가져간 well이 있으면 저장하지 않고 알린다. 없으면 worksheet를 게시한 뒤 사용 기록을 덧붙인다. 같은 실험을 다시 저장해도 두 번 기록하지 않는다. `Save elsewhere`로 저장한 사본은 ECHO가 실행하는 파일이 아니므로 기록하지 않는다.
- 공용 폴더에 접근할 수 없으면 worksheet 저장도 원래 실패하므로 새 실패 경우는 없다. 계획 단계에서는 "저장할 때 다시 확인한다"고 안내한다.
- plate 고르기: Plate 칸이 드롭다운이다. 공용 기록이 있는 plate가 "이름 · 종류 · 사용한 well 수, next 다음 well"로 나오고(열 때마다 다시 읽음), 고르면 이름·종류·다음 well이 함께 정해진다. 기록마다 plate 종류를 적어 두므로 종류가 다른 plate type을 고르면 받지 않는다(예: "SOLVENT-01 is a 1536LDV plate").
- `New plate…`: Plate 드롭다운 자체가 글자를 칠 수 있는 칸이 된다(초점이 다른 칸으로 옮겨 가지 않음, 자동 완성 없음, Enter로 확정, Esc·Cancel로 되돌림, 비워 둬도 저절로 닫히지 않음), 새 plate는 A1부터 쓴다. 이미 공용 plate인 이름(대소문자 무관)을 넣으면 새로 만들지 않고 그 plate를 이어 쓴다. 직접 타이핑은 이 경로뿐이라 오타로 새 plate가 생기지 않는다. 새 plate는 첫 사용이 기록될 때 목록에 나타나며, 그 전에는 "이름 · not a shared plate yet"으로 보인다.
- `Preparation`(이전 Dispensing source): 화면 폭 전체를 쓰고, 맨 위에 준비물 목록을 둔다. `Crystal drops`(필요한 결정 수 = well 수, 조건 수 × 조건별 수)와 첨가제마다 채울 well과 양. 그 아래가 plate·plate type·wells 입력이고, 문제가 있을 때만 주황 문구가 나온다. Worksheets 단계의 "Prepare before dispensing"에도 Crystal drops 줄을 넣었다.
