# Astra 제안서 — 실험 선택에서 worksheet까지 안내하는 XtalFlow

작성일: 2026-09-17  
상태: UI/UX 설계 제안 · 구현 진행 중(`experiment-first` 브랜치)  
참고 소스: `b458bbb` 및 `codex/astra_ui_proposal_ko.md`

## 1. 목표와 전제

처음 soaking 실험을 준비하는 연구자가 화면의 안내만으로 실험 유형을 고르고, 이미지를 검토하고, 분주 위치와 조건을 확인해 올바른 worksheet를 저장하도록 한다. 화면은 짧은 안내, 분명한 다음 행동, 넓은 이미지와 읽기 쉬운 표로 구성한다.

이 저장소의 소스와 화면이 **최신 버전**이며 이 제안서가 재설계하는 대상이다. 구현된 기능, 데이터 의미, 운영 환경, 기존 조작을 기준으로 삼되, 현재 탭과 클래스 구조를 설계 제약으로 보지 않는다. 아래의 새 화면과 동작은 제안이다.

이전 제안서는 Image Review와 Planning을 정돈하는 데 초점을 맞췄다. 이 문서는 그다음 설계로, **사용자의 실험 목적을 먼저 정하고 필요한 화면을 연결**한다. 이전 제안서의 배치와 충돌하는 부분은 이 문서의 실험 중심 흐름을 우선한다.

이미지에서 어떤 결정이 soaking에 적합한지는 단백질, plate, 실험 조건에 따라 달라진다. UI만으로 과학적 판단을 보장할 수 없으므로 연구실에서 검증한 적합/부적합 예시와 분주 위치 안내를 제품 안에 제공한다. 자동 경계 검출 점수는 결정 품질이나 soaking 성공 확률이 아니다.

## 2. 핵심 권고: 시작 화면 + 작업 단계

실험 유형을 먼저 고르는 구조를 권장한다. Raw Crystal에는 library와 ECHO가 나타나지 않고, Fragment Screening에는 필요한 조건만 나타나므로 불필요한 판단이 줄어든다.

처음부터 8–9개 탭을 만들면 작업이 길게 느껴진다. 시작 화면 이후에는 다섯 단계로 묶고 세부 작업은 각 화면 안에 둔다.

| 단계 | 사용자 질문 | 주요 작업 |
|---|---|---|
| 1. Setup | 무엇을 준비하는가? | Protein, 이름, 실험 유형 확인 |
| 2. Select wells | 어떤 well을 쓸까? | Plate 로딩, 이미지 검토, position 선택 |
| 3. Conditions | 무엇을 얼마나 적용할까? | Library·조건 선택과 배정 |
| 4. Review | 이 결과로 진행해도 될까? | 선택·좌표·배정 검토와 확정 |
| 5. Worksheets | 파일을 어디에 저장할까? | Preview와 저장, 선택적 MxLive 전달 |

Raw Crystal은 Conditions가 필요 없으므로 `Setup → Select wells → Review → Worksheets` 네 단계만 표시한다. 초보자/숙련자 모드를 따로 만들지 않고, 안내를 접으면 같은 화면을 간결하게 쓸 수 있게 한다.

## 3. 시작 화면

```text
XtalFlow                                         [Recent work] [Settings]

What would you like to prepare?
Choose an experiment. We will guide you to the worksheets.

┌ Fragment Screening ─────────────┐ ┌ Raw Crystal ────────────────────┐
│ Add library fragments to wells.│ │ Harvest without soaking.       │
│ ECHO + SHIFTER worksheets       │ │ SHIFTER worksheet              │
│ [Start Fragment Screening]     │ │ [Start Raw Crystal]            │
└────────────────────────────────┘ └────────────────────────────────┘

Recent work
BRD4 Core Screen    Draft · Select wells    Edited today     [Resume]
CypA Raw Crystals   Finalized · Files ready Edited yesterday [Open]

[More experiment types ▾]
```

구현된 두 유형만 시작 가능한 항목으로 크게 표시한다. Solvent / Duration Test, Cryo Test, Custom Soaking은 구현 전까지 접힌 `More experiment types` 안에 `Not available yet`로 둔다. 쓸 수 없는 선택지가 첫 화면의 대부분을 차지하지 않게 한다.

Recent work는 실험 이름, 유형, 마지막 유효 단계, 마지막 수정 시각을 보여준다. `Resume`은 마지막으로 머문 위치를 열고, 그 뒤 발견한 문제는 그 화면에서 알린다. 문제가 있다는 이유만으로 다른 단계로 강제 이동시키지 않는다.

Workspace는 plate 목록과 보정 같은 공용 정보를 담는 저장 공간으로 유지하되 시작의 필수 질문에서 뺀다. 기본 저장 공간을 자동으로 연결하고, 필요한 사용자만 `Use an existing workspace…`로 고른다.

## 4. 공통 화면 골격

```text
[‹ Experiments]  BRD4 Core Screen                 Draft · Saved locally
Fragment Screening

[1 Setup ✓] — [2 Select wells ●] — [3 Conditions] — [4 Review] — [5 Worksheets]
──────────────────────────────────────────────────────────────────────────
Select wells
Click where the compound should be dispensed.             [Show examples]

                        현재 작업 내용

──────────────────────────────────────────────────────────────────────────
12 wells · 19 positions                         [Back] [Continue to Conditions]
✓ Saved locally                                            [Details]
```

상단은 실험 정체성, stepper는 현재 위치, 본문은 지금 할 작업, 하단은 완료 조건과 다음 행동을 담당한다. 화면마다 파란 primary action은 하나만 강조한다. 단계 이동에 저장 확인창을 띄우지 않지만, 실제 저장 실패가 있으면 이유와 `Retry`를 보여준다.

완료했거나 편집할 수 있는 단계는 stepper를 눌러 돌아갈 수 있다. 뒤 단계를 미리 볼 수는 있지만 입력이 부족하면 빈 표 대신 필요한 입력과 이동 링크를 보여준다. `Review`와 실제 저장에는 검증 조건을 적용한다.

## 5. Setup — 처음에는 최소한만 묻기

```text
Experiment details
Give this experiment a protein name.

Experiment type    Fragment Screening
Protein *          [BRD4                           ]
Experiment name    [BRD4 fragment screen            ]
                   You can change this name later.

[Use a previous setup…]
                                           [Continue to Select wells]
```

Protein은 필수, 실험 이름은 자동 제안 후 수정할 수 있다. Experiment ID는 확정할 때 정해지므로 처음부터 입력받지 않는다. Library, 행 범위, 부피는 Conditions에서 정한다. 숙련자는 이전 설정을 불러올 수 있다.

실험 유형을 고르는 순간 **selection이 비어 있는 준비 초안**을 저장한다. 기존의 "Project 생성 = selection snapshot 고정"과 수명 주기가 다르므로, 준비 초안 생성과 selection 고정을 서로 다른 사건으로 다룬다.

## 6. Select wells — 로딩과 이미지 검토를 한 단계로

plate가 없으면 이미지 대신 다음을 보여준다.

```text
Load your crystallization plates

Plate type *    [Choose plate type ▾]
Plate codes *   [2069, 2070                       ]
[✓] Use latest batch and profile

[Load plates]
```

plate type은 이미지의 d1/d2/d3 존재 여부로 추측하지 않고 사용자가 명시적으로 고른다. 최신 batch/profile 사용을 끄면 같은 화면에서 plate별로 고른다. 일부 plate만 실패하면 성공한 목록은 유지하고 실패한 plate에 `Retry`를 표시한다.

이미지가 로드되면 검토 화면으로 전환한다.

```text
Select wells                                        [Show examples]
Click where the compound should be dispensed.

┌ Plates ────────┬───────────────────────────────────────────────────┐
│ 2069          │ A04a    7 / 192   [All images ▾] [Well A04a] [◀][▶]│
│ 12 selected   ├───────────────────────────────────────────────────┤
│               │                                                   │
│ 2070          │                 CRYSTAL IMAGE                     │
│ 0 selected    │                                                   │
│               │                      +                            │
│ [+ Load]      │                                                   │
├───────────────┴───────────────────────────────────────────────────┤
│ [−] 100% [+] [Fit]                  ✓ Well boundary accepted [Edit]│
│ Targets/img [2]  Auto-next                    This well: 1 position│
└───────────────────────────────────────────────────────────────────┘
12 wells · 19 positions      [Review selected wells]   [Continue to Conditions]
```

position을 하나 이상 지정하면 그 well이 선택된다는 기존 원칙을 유지한다. well 선택용 체크박스는 따로 두지 않는다. 왼쪽 클릭 추가, 오른쪽 클릭 삭제, 좌우 키 이미지 이동, 상하 키 plate 이동을 유지한다.

**position은 실험마다 따로 저장한다.** 새 실험의 Select wells는 빈 상태로 시작하고, 같은 워크스페이스의 다른 실험에서 쓴 well은 `Used in <실험 이름>`으로만 표시한다. well 경계(보정)는 물리 정보이므로 워크스페이스 안에서 공유한다. 기존 워크스페이스에 실험에 쓰이지 않은 position이 있으면, 새 실험을 시작할 때 `이 워크스페이스에 이전 검토 position N개가 있습니다. 이 실험에 사용할까요?`로 제안한다.

`Targets/img`는 사용자 전체 설정인 auto-next 기준이며, 필요할 때만 `You may move on with fewer positions.`를 보여준다. 기준보다 적게 찍은 이미지를 미완료로 취급하지 않는다. 선택 well 수에 정해진 목표를 두지 않고, 나중에 library 개수와 비교한다.

## 7. 초보자가 "무엇을 고를지" 배우도록 돕기

`Show examples`는 이미지 옆에 접이식 패널을 연다. 연구실이 승인한 실제 이미지 위에 고를 예시, 피할 예시, 분주 위치 설명을 겹쳐 보여주고, 설명은 이미지마다 한 문장으로 한다.

패널이 다룰 내용은 결정의 모습, well 경계와 안쪽 drop의 차이, soaking position과 결정 위치의 차이, 판단이 어려울 때의 처리 방법이다. 구체적인 적합 기준이나 안전한 분주 거리를 개발자가 추측해 고정하지 않는다. 예시 이미지와 설명은 설정한 폴더에서 읽고, 없으면 `No examples configured`를 표시한다.

보정 상태는 `Boundary detected — check alignment`, `Boundary accepted`, `Boundary missing`으로 구분한다. confidence 수치를 결정 품질로 오해하지 않게 한다. 잘못 검출되면 같은 이미지에서 `Set boundary with 3 points`를 실행하고 `Click 3 points on the well edge · 1/3`처럼 진행을 안내한다.

## 8. Selection 확인과 수정

```text
Review selected wells                  12 wells · 19 positions
[All selected ▾] [Needs attention 2]

Plate  Well   Positions   Status                         Action
2069   A04a   2           Boundary not accepted           [Check]
2069   A04c   1           Position outside well           [Fix]
2070   A01a   1           Ready                          [View]

                 선택한 행의 이미지와 position 표시

2 wells need attention. No wells will be left out.
[Back to images]                              [Check next issue]
```

경고 행을 고르면 그 이미지를 보여주고 경계 수정이나 position 삭제로 바로 이동한다. 해결하지 않은 항목을 자동으로 제외하지 않는다. 모든 항목이 유효해지면 `Use these 12 wells`를 보여준다.

position이 실험마다 따로 저장되므로, 다른 실험의 선택을 바꿔도 이 실험의 선택은 변하지 않는다. 확정된 revision은 그 시점의 선택을 snapshot으로 보존하므로, 확정 뒤 well을 추가·삭제하면 새 Draft 변경이 되고 영향받는 단계에 `Needs review`를 표시한다.

## 9. Conditions — Fragment Screening 입력

```text
Assign fragments to 12 selected wells
One fragment is assigned to each well.

Library *          [Core library ▾]                  [Refresh]
Fragments          (●) All fragments  ( ) Choose rows
Data rows          [1–12                        ]
                   Data rows start at 1; the header is not counted.

Total volume/well * [25.0] nL
                   Shared equally between positions in each well.

Assignment order   [Keep selection order ▾]
12 fragments → 12 wells                              ✓ Counts match
```

**개수 규칙**
- fragment가 well보다 적으면 진행을 막고 `12 wells, 10 fragments — choose 2 more fragments or edit the selected wells.`처럼 해결 방법을 보여준다.
- fragment가 well보다 많으면 진행을 허용하되 `15 fragments → 12 wells · the last 3 are not used`로 쓰지 않는 fragment를 분명히 표시한다. Review에서도 같은 내용을 보여준다.

**부피 규칙**
- 분주 단위는 2.5 nL다. 총량을 well 안의 position 수로 나눴을 때 모든 position에 2.5 nL 단위로 **똑같이** 나눌 수 없으면 진행을 막는다.
- 막을 때는 가능한 가까운 총량을 제안한다. 예: `25 nL cannot be split equally between 3 positions. Use 22.5 nL or 30 nL, or change the positions.`
- 조용히 반올림하거나 나머지를 한 position에 몰아주지 않는다.

배정 순서는 `Keep selection order`와 `Plate and well order` 중에서 고른다. 배정 결과를 본 뒤 순서를 바꾸면 이전→새 대응 미리보기와 `Apply reassignment`를 보여준다. 표 머리글을 눌러 화면 정렬을 바꾸는 것이 실제 배정을 바꾸지 않도록 구분한다.

## 10. Review — 배정 내용과 확정을 한 화면에서

```text
Review your experiment
BRD4 · Fragment Screening · 12 wells · 19 positions

Plate Well Positions Fragment Source       Total/well Per position Previous use
2069 A04a    2       CMP-001  SRC-1 / A01   25 nL      12.5 nL      First use
2069 A04c    1       CMP-002  SRC-1 / A02   25 nL      25.0 nL      Reused

[View selected image] [Preview ECHO] [Preview SHIFTER]

✓ Required details complete  ✓ Coordinates valid  ✓ Assignments checked
Experiment ID: determined when finalized

Finalize saves a fixed version for the worksheets. It does not run the experiment.
[Back to Conditions]                              [Finalize and continue]
```

Finalize는 늘 떠 있는 탭으로 만들지 않고, Review 안의 짧은 설명과 하나의 action으로 표현한다. 최종 확인창에는 확정될 ID, well 수, position 수, 저장될 장비를 보여준다. 확정 뒤 수정은 새 Draft 변경으로 다루며 이전 revision과 이미 저장한 출력은 바꾸지 않는다.

재사용은 진행을 막지 않는다. `Reused`에서 이전 실험 이름과 기록 상태를 보여주되, Finalized/Uploaded 기록을 실제로 실험을 수행한 증거로 단정하지 않는다.

## 11. Worksheets — 저장 위치와 결과 보여주기

```text
Your worksheets are ready                         Finalized · Revision 1
FragSC-202609-BRD4-01

ECHO 650   19 transfer rows                  [Preview]
           /smbmount/echo650/<user>/...
SHIFTER 1  12 sample rows                    [Preview]
           /smbmount/shifter1/<user>/...
SHIFTER 2  12 sample rows                    [Preview]
           /smbmount/shifter2/<user>/...

[Back to Review]                                   [Save all worksheets]

[Optional: Send records to MxLive ▾]
```

장비 행은 **설정된 장비 목록**(`[[instruments]]`)에서 만든다. 실험이 만드는 worksheet 종류(ECHO/SHIFTER)를 읽는 모든 장비가 표시된다.

기본 primary action은 `Save all worksheets`다. 장비별로 따로 저장하게 해서 하나를 빠뜨리는 일이 없게 한다. Raw Crystal은 ECHO 행 없이 `Save SHIFTER worksheets`로 표시한다.

저장은 **전부 성공하거나 전부 취소된다.** 한 장비 폴더라도 실패하면 이미 쓴 파일까지 지워서, 장비가 일부만 저장된 실험을 실행하지 않게 한다.
- 성공: `Worksheets saved`, 장비별 경로, 저장 시각, revision, `Open folder` / `Copy path`. 이 화면을 사용자의 당면 완료 지점으로 삼는다.
- 실패: `No worksheets were saved. Could not write to SHIFTER 1.`과 함께 `Retry` 또는 `Choose another location…`을 보여준다.
- 대체 위치에 저장하면 `Saved to an alternate location`으로 표시하고, 장비 PC가 그 위치에 접근할 수 있는지는 확인되지 않았다고 짧게 알린다.

MxLive가 설정되지 않아도 worksheet 저장을 막지 않는다.

## 12. Raw Crystal과 이후 실험

Raw Crystal은 `Setup → Select wells → Review → Worksheets`다. 실험을 고를 때 `Harvest without soaking. SHIFTER worksheet only.`로 설명하고, library·부피·ECHO는 숨긴다. SHIFTER worksheet는 well당 한 행이므로 Raw Crystal의 position은 "이 well의 결정을 수확한다"는 표시다. 안내 문구는 `Click the crystal to harvest`로 두되, 실제 수확 실무와 맞는지 확인한 뒤 확정한다.

이후 실험은 같은 화면 골격에 전용 Conditions 편집기를 더한다.

| 실험 | Conditions 후보 | 확정해야 할 사양 |
|---|---|---|
| Solvent / Duration Test | 조건 행렬(용매 × 최종 농도 × 시간), 반복 수, 대조군 | drop 부피 출처, 수확 시각 기준점 |
| Cryo Test | Cryoprotectant 조건, 반복 수, 순서 있는 복합 처리 | 넣는 방법, 필요한 worksheet |
| Custom Soaking | 승인된 template, 조건 행, 배정 | 복수 첨가, 부피 제약, 혼합 가능 여부 |

Solvent/Cryo 조건 행렬의 상세 설계는 `codex/condition_matrix_experiments_ko.md`에서 다룬다. 정해지지 않은 실험에 ECHO 출력을 일괄 약속하지 않는다.

## 13. 상태, 의존 관계, 되돌아가 수정하는 규칙

화면 위치와 실험의 유효성을 따로 관리한다. stepper로 돌아가는 것만으로 기존 결과를 버리지 않는다. 저장 상태와 실험 수행 상태도 구분한다.

| 상태 축 | 표시 | 의미 |
|---|---|---|
| 편집 저장 | Saving / Saved locally / Save failed | 로컬 변경의 저장 결과 |
| 입력 검증 | Missing details / Selection needs review / Assignment needs review / Ready | 확정에 필요한 조건 |
| Revision | Draft / Finalized r1 / Changes after r1 | 확정 내용과 현재 편집의 관계 |
| 출력 | Ready to save / Saved / Save failed | revision별 파일 전달 상태 |
| MxLive | Not configured / Ready / Uploaded / Partial / Unknown | 외부 업로드 결과 |

`Ready to finalize`는 필수 입력, selection, 보정, 배정, 장비 제약, 로컬 저장이 모두 유효할 때만 계산한다. 실험 ID에 필요한 중복 확인도 같은 검증에 포함한다. 위젯마다 따로 ready를 판단하지 않는다.

| 변경 | 다시 확인할 것 | 유지하는 것 |
|---|---|---|
| 실험 표시 이름 | 보통 표시만. 출력에 포함되면 출력 | selection, 배정 |
| Protein | 이름 규칙, 기록, Review | plate와 position |
| Plate format / 보정 | well 주소, 좌표, 출력 | 원본 이미지와 이전 revision |
| well 추가·삭제 | fragment 배정, 개수, 모든 출력 | 유효한 기존 position, 이전 revision |
| position 추가·삭제 | 분할 부피, 좌표, ECHO 행 | well 단위 fragment 대응(유지 가능할 때) |
| Library / rows / order | fragment 배정, Review, 출력 | selection |
| 총 부피 | 분할 부피, 장비 제약, 출력 | selection과 fragment 대응 |
| 저장 위치 | 전달 상태와 재저장 | 확정 revision, 과학적 내용 |

영향받는 단계에 `Needs review`를 붙인다. 확정된 r1 이후에 편집해도 r1의 저장·업로드 기록은 남기고, 현재 Draft를 저장 완료로 표시하지 않는다. **worksheet는 화면의 현재 값이 아니라 선택된 확정 revision에서 만든다.**

## 14. 영어 UI 문구

| 상황 | 문구 |
|---|---|
| 시작 | What would you like to prepare? |
| 이어하기 | Resume experiment |
| 선택 부족 | Select at least one well to continue. |
| Protein 없음 | Enter a protein name to continue. |
| 보정 필요 | 2 wells need a boundary check. |
| 수정으로 이동 | Check next issue |
| 재배정 | Changing the order will assign fragments to different wells. |
| 확정의 의미 | Save a fixed version for the worksheets. This does not run the experiment. |
| 확정 후 편집 | Your changes are saved as a draft. Revision 1 is unchanged. |
| 저장 실패 | No worksheets were saved. Could not write to SHIFTER 1. |
| 저장 성공 | ECHO and SHIFTER worksheets saved. |
| 오프라인 | Images are unavailable. Your experiment is saved locally. |

오류는 해당 입력 가까이에, 실험 전체의 문제는 하단에 짧게 표시한다. 비활성 버튼의 이유를 tooltip에만 숨기지 않는다.

## 15. 시각 체계, 단축키, 작은 화면

배경 `#F4F6F8`, 표와 입력면은 흰색, 기본 글자 `#202A35`, 보조 글자 `#596575`를 쓴다. 파란색은 primary action과 focus, 초록은 확인된 결과, amber는 확인 필요, 빨강은 진행을 막는 문제에만 쓴다. 기호와 문구를 함께 써서 색에만 의존하지 않고, 최종 색은 실제 글자 크기와 배경에서 대비를 확인한다.

여백은 4/8/12/16 체계를 쓰고, 일반 입력은 320–480 정도로 폭을 제한해 Protein 칸이 화면 전체를 채우지 않게 한다. 표 행 높이는 28–32, 숫자는 오른쪽 정렬, 단위는 머리글에 표시한다. OS 글자 크기 확대를 따르고 고정 px에만 의존하지 않는다. 그림자와 장식 카드를 많이 쓰지 않는다.

1280×800에서는 stepper를 짧은 이름으로 보여주고 plate 목록을 접을 수 있게 한다. 요약은 작은 화면에서 오른쪽 overlay, 큰 화면에서 dock으로 둔다. 현재 well과 primary action은 항상 보이게 하고, 주 표 외에 이중 스크롤 영역을 늘리지 않는다.

| 입력 | 동작 |
|---|---|
| 이미지에서 왼쪽/오른쪽 클릭 | position 추가/삭제 |
| 이미지에서 ←/→ | 이전/다음 이미지 |
| 이미지·plate 목록에서 ↑/↓ | plate 이동 |
| 요약에서 ↑/↓ | 행 선택과 이미지 표시, focus는 요약에 유지 |
| Well 칸에서 Enter / Esc | 이동 / 입력 취소 |
| 3점 보정 중 Esc | 보정 취소 |
| 이미지에서 0 / + / − | Fit / 확대 / 축소 |
| Ctrl/Cmd+L | Well 입력으로 focus |
| ? | 단축키 목록 |

입력 칸의 화살표 키를 이미지 이동에 빼앗지 않는다. 안내는 화면당 1–2문장을 기본으로 하고 접힘 상태를 사용자 설정에 저장한다. 이미지를 넘기거나 position을 추가할 때마다 popup을 띄우지 않는다.

## 16. 구현 방침과 이전

| 안 | 내용 | 초보자 / 숙련자 | 작업량·회귀 위험 |
|---|---|---|---|
| A | 지금 탭에 시작 화면과 길 안내만 추가 | 초보자 입구는 개선, 뒤 단계는 그대로 | Small–Medium / Low–Medium |
| B | PyQt를 유지하고 실험 중심 화면과 전환을 구성 | 공통 화면에서 안내와 빠른 작업을 함께 | Large / Medium–High |
| C | 범용 template engine과 실험 수행 추적 포함 | 장기 확장성은 높지만 확정할 사양이 많음 | Large / High |

**B를 권장한다.** 검증된 이미지 좌표, plate 주소, library 읽기, worksheet 생성 코드를 재사용하고, UI 아래에 작은 workflow controller와 공통 검증 결과를 둔다. 처음부터 범용 engine을 만들지 않는다.

이전 순서:

1. ready 판단이 화면마다 다른 문제를 없애고, 필수 조건과 이유를 하나의 검증으로 모은다. worksheet는 확정 revision에서 만든다.
2. 실험별 position 저장과 selection 없는 준비 초안을 도입한다(DB 스키마 변경, 자동 백업).
3. 시작 화면, Recent work, 공통 화면 골격을 추가하고 Raw Crystal을 네 단계로 끝까지 통과시킨다.
4. Fragment Screening의 Conditions, 재배정 확인, Review를 추가한다.
5. Worksheets 저장 화면, 대체 위치, MxLive, 이어하기를 정리한다.
6. 예시 패널, 작은 화면, 기존 Planning 코드 정리.
7. 실제 초보자와 숙련자로 평가한다.

기존 DB의 검토 정보와 확정 revision은 보존하고, 화면 추가를 이유로 재배정하거나 revision을 다시 만들지 않는다. 기존 Draft는 저장된 정보로 완료 단계를 추정하되, 사용자가 Review를 봤는지까지 추정해 완료로 처리하지 않는다.

## 17. 완성 판정과 먼저 만들 화면

초보자 테스트: 화면의 예시를 참고해 2 plate 로드, 10 well 선택, 잘못된 경계 1건 수정, 10 fragment 선택, 배정 확인, 확정, worksheet 저장까지 진행한다. 진행자 도움 없이, 몰래 빠진 well 없이, 의도하지 않은 재배정 없이 끝내는 것이 목표다. primary action을 5초 안에 찾는지 관찰한다. 선택의 과학적 적합성은 연구실 숙련자가 따로 평가한다.

숙련자 테스트: 이미지 100장 검토, 요약에서 10건 재확인, 이전 설정을 이용한 출력. 현행 버전의 소요 시간과 조작 수를 기준으로, 안내 때문에 반복 popup이나 불필요한 이동이 늘지 않았는지 측정한다.

먼저 만들 화면 7개:

1. 실험 선택과 Recent work
2. plate 로드 전의 Select wells
3. 이미지와 선택 수 중심의 Select wells
4. 경고가 있는 selection 확인과 경계 수정
5. Fragment 조건과 개수 비교
6. 배정 Review와 Finalize 설명
7. Worksheet 저장 성공·실패

구현 전에 확인할 사항: 연구실이 승인한 선택 예시 이미지, Raw Crystal position의 의미, 조건 행렬 실험의 drop 부피 출처와 수확 시각 기준점. 확인되지 않은 값을 편의상 기본값으로 고정하지 않는다.

이 설계의 완성 조건은 사용자가 "무엇을 준비하는지", "다음에 무엇을 할지", "무엇이 부족한지", "어떤 파일이 어디에 저장됐는지"를 화면만 보고 설명할 수 있는 것이다.

## 18. 구현 현황 (`experiment-first` 브랜치)

2026-09-17 기준으로 1–11절의 흐름을 구현했다. 제안과 다르게 정한 부분은 다음과 같다.

| 항목 | 구현 | 이유 |
|---|---|---|
| Workspace 선택 | 시작 화면 아래의 `New experiments use workspace` 한 줄. Setup에는 읽기 전용으로 표시 | 실험을 만든 뒤 workspace를 바꾸면 실험별 position과 plate 연결이 끊긴다 |
| 이전 position | 새 실험 시작 시 어느 실험에도 속하지 않은 workspace position이 있으면 한 번 묻고 복사 | 스키마 18 이전에 찍은 position을 잃지 않게 하기 위함 |
| 다른 실험이 쓴 well | Select wells의 현재 이미지 설명에 `also used in <실험 이름>`, Review 표의 Usage 열 | 같은 결정을 두 실험에 쓰는 실수를 막되 막지는 않는다 |
| Conditions의 순서 변경 | 배정이 바뀌는 well 목록을 먼저 보여주고 `Apply reassignment` / `Keep current order` | 10절의 의도하지 않은 재배정 방지 |
| Finalize | Review의 `Finalize and continue`가 실험 ID, well·position 수, 대상 장비를 확인창으로 보여준다 | 확정이 실험 실행이 아님을 알림 |
| Worksheet 저장 실패 | 화면 안에 "No worksheets were saved"와 `Try again`, `Save to another folder…` | 모달 선택 대신 결과 영역에서 복구 |
| 예시 이미지 | Select wells의 `Show examples` 창. `--examples-dir` 폴더의 이미지와 같은 이름의 `.txt` 설명 | 연구실 승인 예시를 코드에 넣지 않기 위함. 폴더가 없으면 설정 방법을 안내 |
| MxLive | Worksheets 아래 접힌 `Send records to MxLive (optional)` | 기존 업로드·결과 모름·Verify 흐름 유지 |

아직 남은 일: 조건 행렬 실험(Solvent / Cryo, `codex/condition_matrix_experiments_ko.md`), Raw Crystal position 의미의 연구실 확인, 연구실 승인 예시 이미지 준비.
