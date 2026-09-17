# XtalFlow 차세대 UI/UX 설계 제안서

> Astra 제안서 · 결정학 기반 FBDD 데스크톱 워크플로

## 1. 문서의 목적과 적용 범위

이 문서는 XtalFlow의 차세대 UI/UX 방향을 정의한다. XtalFlow는 DLS XChem과
유사한 결정학 기반 FBDD 영역에서, 결정 이미지 검토부터 soaking 위치 선택,
worksheet 생성, MxLive 업로드까지 연결하는 데스크톱 프로그램이다.

### 현재 소스에 관한 전제

이 저장소의 XtalFlow 소스와 실행 화면이 **최신 버전**이며, 이 제안서가 재설계하려는
대상이다. 현재 소스와 스크린샷은 다음 기준으로 사용한다.

- 현재 지원 기능과 실제 FBDD 작업 흐름
- 화면에 필요한 데이터와 상태
- 기존 사용자가 익숙한 키보드·마우스 조작
- 제거하면 안 되는 기능과 운영 환경 제약

반면 현재 위젯 배치, 화면 비율, 정보 구조, 메뉴 구성, 색상과 명칭은 이 제안서가
개선하려는 대상이므로 최종 디자인의 기준으로 삼지 않는다.

## 2. 설계 목표

차세대 UI는 다음 목표를 동시에 만족해야 한다.

1. 수백 장의 crystal image를 키보드와 마우스로 빠르게 검토할 수 있어야 한다.
2. 현재 이미지, well, soaking position과 전체 진행 상황을 즉시 파악할 수 있어야 한다.
3. 자동 저장, Plan 확정, worksheet 저장, WebDB 업로드 상태를 혼동하지 않아야 한다.
4. 경고가 발생하면 사용자가 해당 이미지와 수정 작업으로 바로 이동할 수 있어야 한다.
5. 작은 Linux 운영 화면과 macOS 개발 환경에서 일관되게 동작해야 한다.
6. 높은 정보 밀도를 유지하면서도 차분하고 현대적인 scientific desktop
   application으로 보여야 한다.

권장 방향은 **PyQt를 유지하면서 화면 구조와 상태 표현을 크게 재설계하는 안**이다.
현재 검증된 이미지 선택, 좌표 변환, worksheet 생성 로직을 유지하면서 사용자가
직접 접하는 작업 흐름을 개선할 수 있다.

## 3. 현재 정보 구조의 문제

현재 화면은 기능의 중요도보다 기능이 추가된 순서에 따라 배치된 경향이 있다.
Plate 로딩은 작업 초기에 몇 차례 수행하지만, 이미지 이동과 soaking position
선택은 수백 번 반복된다. 그러나 두 작업이 화면에서 비슷한 시각적 무게를 갖는다.

Calibration 역시 정상일 때는 별도 조작이 필요하지 않지만 여러 버튼과 기술 수치가
항상 노출된다. `Selected`, `Session total`, `Reviewed`, `Plate matches`는 범위가
서로 다른데 한 줄에 나열되어 사용자가 숫자의 의미를 먼저 해석해야 한다.

Planning 화면에서는 입력값, 배정 결과, worksheet, Finalize와 MxLive 업로드가
기능적으로 연결되어 있지만 시각적으로는 작업 단계와 상태 관계가 충분히 드러나지
않는다.

## 4. 전체 Navigation 구조

상위 작업 공간은 두 개로 유지한다.

- **Image Review**: plate 이미지 검토와 soaking position 지정
- **Planning**: selected wells를 고정한 Project와 실험 조건 관리

두 작업은 단방향 wizard가 아니다. Plan을 준비하다가 이미지를 다시 확인할 수 있고,
다음 Project를 위해 selection을 수정할 수도 있어야 한다.

```text
┌ XtalFlow ─ Workspace: BRD4 pilot ▾ ──────────────────────── ⋯ ┐
│ [ Image Review ] [ Planning · 3 ]                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│                     현재 작업 화면                          │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ ✓ Saved locally      현재 이미지 경로 / 작업 메시지      ⓘ  │
└─────────────────────────────────────────────────────────────┘
```

Workspace의 생성과 이름 변경은 selector 옆 `⋯` 메뉴로 통합한다. Project는 특정
시점의 selected wells와 하나의 Plan을 결합한 실험 단위이므로 UI에서는
`New Project…`라는 표현을 사용한다.

## 5. Image Review 재설계

왼쪽에는 plate 목록, 가운데에는 이미지, 오른쪽에는 필요할 때 여는 Target Summary를
둔다. 반복 탐색은 이미지 위, 표시와 calibration 조정은 이미지 아래에 배치한다.

```text
┌ Workspace: BRD4 pilot ▾ ─────────────────────────────────────┐
│ [ Image Review ] [ Planning · 3 ]                            │
├────────────────┬────────────────────────────────────────────┤
│ PLATES     [+] │ Plate 2069 / A04a     7 / 192    [◀] [▶]   │
│ Find plate…    │ [All images ▾]          [Well: A04a      ] │
│                ├────────────────────────────────────────────┤
│ ▌2069          │                                            │
│  3-lens        │                                            │
│  42/192 seen   │              CRYSTAL IMAGE                 │
│  12 selected   │                                            │
│                │                    +                       │
│  2070          │                                            │
│  3-lens        │                                            │
│  0/192 seen    │                                            │
│                ├────────────────────────────────────────────┤
│                │ [−] 100% [+] [Fit]     ✓ Well aligned [⋯] │
│                │ Targets/img [2 ↕]   This well: 1 position │
├────────────────┴────────────────────────────────────────────┤
│ Selection: 12 wells · 19 positions  [Target Summary]         │
│                                      [New Project…]         │
├─────────────────────────────────────────────────────────────┤
│ ✓ Saved locally                  …/wellNum_4/…/d1_…jpg      │
└─────────────────────────────────────────────────────────────┘
```

### 위치별 책임

| 위치 | 내용 | 목적 |
|---|---|---|
| 왼쪽 | Plate 목록과 plate 관리 | Plate 이동과 상태 파악 |
| 이미지 위 | Well, 순서, 필터, 이전/다음 | 반복 탐색 집중 |
| 이미지 아래 | Zoom, Fit, calibration | 이미지 표시와 좌표 조정 |
| Selection bar | Well 수, position 수, Project 생성 | Review 결과를 Planning으로 연결 |
| Status bar | 저장 상태, 경로, 작업 메시지 | 시스템 상태 확인 |

`Targets/img`는 필수 목표가 아니라 auto-next 기준이다. 값보다 적은 position을
선택하고 수동으로 다음 이미지로 이동하는 동작을 자연스럽게 허용한다.

## 6. Target Summary

Target Summary는 오른쪽에서 여는 검토 패널로 유지한다. 다만 selection의 범위를
명확히 표시해야 한다.

- Image Review: `Workspace selection · Live`
- Planning: `Project selection · Snapshot`

### 닫힌 상태

```text
┌ Plates ┐┌──────────────────── Image ────────────────────┐
│        ││                                               │
│        ││                                               │
└────────┘└───────────────────────────────────────────────┘
Selection: 12 wells · 19 positions        [Target Summary]
```

### 열린 상태

```text
┌ Plates ┐┌──────────── Image ────────────┬ Selection · Live ┐
│        ││                              │ [Warnings 2 ▾] × │
│        ││                              │ Plate Well Pos  │
│        ││                              │ 2069  A04a  1    │
│        ││                              │ 2069  A04a  2    │
│        ││                              │ 2069  A04c  1    │
│        ││                              │ ...              │
└────────┘└──────────────────────────────┴──────────────────┘
```

행을 선택하면 해당 이미지를 즉시 표시한다. 위·아래 방향키로 행을 이동하며 현재
position을 이미지 위에서 강조한다. 경고 셀에서는 문제 이미지와 수정 작업으로 바로
이동한다. 여러 행을 선택해 position을 삭제할 수 있으며 삭제 개수를 명확히 표시한다.

작은 화면에서는 이미지 오른쪽에 overlay로, 큰 화면에서는 resizable dock으로
표시한다. Summary가 열리거나 닫힐 때 이미지 좌표계와 zoom 상태가 안정적으로
유지되어야 한다.

## 7. Calibration UI

정상 상태에서는 짧은 상태와 하나의 진입점만 표시한다.

```text
✓ Well aligned · Auto        [Adjust…]
```

검토가 필요하면 다음과 같이 표시한다.

```text
△ Check well boundary        [Accept] [Adjust…]
```

Calibration이 없으면 다음 동작을 제시한다.

```text
! Well boundary unavailable   [Detect] [Set 3 points]
```

`Adjust…`를 누르면 이미지 근처에 상세 패널을 연다.

```text
Well calibration
Method: Automatic
Detection score: 94%
Diameter: 2.77 mm
Scale: 2.854 µm/px

[Detect again]  [Set 3 points]
[Accept boundary]

Automatic acceptance
[✓] Enabled for this plate
Minimum score [90%]

[Advanced details ▾]
```

자동 검출 결과는 확정 전에는 점선, 확정 후에는 실선으로 그린다. 색상과 함께
`Auto accepted`, `Manually accepted` 같은 문구를 제공한다.

## 8. Plate 관리 UI

Plate type, codes, batch와 profile 선택을 하나의 `Load Plates…` 대화상자에 모은다.

```text
Load Plates

Plate type [Swissci Midi 3 Lens ▾]
Plate codes [2069, 2070                  ]

[✓] Use latest batch and profile for all

[Cancel]                            [Load]
```

최신값 사용을 해제하면 같은 대화상자 안에 각 plate의 선택 표를 펼친다.

```text
Plate    Batch             Profile
2069     [14121 ▾]         [profileID_1 ▾]
2070     [14122 ▾]         [profileID_1 ▾]
```

Plate 목록에서는 code, 선택된 well 수, review 진행률을 우선 표시한다. batch와
profile은 상세 또는 tooltip에 둔다. 동일한 plate code의 image set이 둘 이상일
때는 구분을 위해 batch/profile을 항상 표시한다.

## 9. Planning 재설계

왼쪽에는 Project 목록, 오른쪽에는 선택한 Project의 정체성, 조건, 배정 결과와
출력 상태를 배치한다.

```text
┌ Workspace: BRD4 pilot ▾ ───────────────────────────────────────────┐
│ [ Image Review ] [ Planning · 3 ]                                  │
├────────────────┬──────────────────────────────────────────────────┤
│ PROJECTS   [+] │ BRD4 · Core library                         [⋯]  │
│ Find project…  │ Fragment Screening · Draft · Saved locally       │
│                │ 48 selected wells · 73 positions · 2 reused      │
│ ▌Core library  ├──────────────────────────────────────────────────┤
│  Draft         │ Plan settings                                [▾] │
│  48 wells      │ Protein [BRD4]   Library [Core library ▾]         │
│                │ CSV rows [1–48]  Vol/well [25.0 nL]              │
│  Raw baseline  │ Assignment: Selection order [Reassign…]           │
│  Finalized r1  ├──────────────────────────────────────────────────┤
│  12 wells      │ [Summary] [ECHO] [SHIFTER] [WebDB]                 │
│                ├──────┬───────┬──────┬────────┬──────────┬─────────┤
│  Pilot screen  │Order │ Plate │ Well │Positions│ Fragment│ Usage  │
│  Uploaded r1   │  1   │ 2069  │ A04a │   2    │ CMP-001 │Original│
│                │  2   │ 2069  │ A04c │   1    │ CMP-002 │Reused  │
│                │ ...                                              │
│                ├──────────────────────────────────────────────────┤
│                │ ✓ Ready to finalize          [Finalize Plan…]    │
├────────────────┴──────────────────────────────────────────────────┤
│ ✓ Saved locally                              [History]             │
└───────────────────────────────────────────────────────────────────┘
```

Raw Crystal Project에서는 library, volume과 ECHO 탭을 표시하지 않는다. Fragment
Screening에서 순서를 바꾸는 작업은 표시순만 바꾸는 것으로 오해되지 않도록
`Reassign…`이라고 표현한다.

Finalize 이후에는 worksheet와 WebDB 전달 상태를 독립적으로 보여준다.

```text
Finalized r1     Worksheets: Not exported     WebDB: Not uploaded

[Save Worksheets…]                         [Upload to MxLive…]
```

## 10. 상태 모델

상태는 한 단어로 합치지 않고 세 축으로 나눈다.

| 축 | 표시 예 | 의미 |
|---|---|---|
| 로컬 저장 | `Unsaved changes`, `Saved locally`, `Save failed` | 현재 변경 저장 여부 |
| Plan 확정 | `Draft`, `Finalized r1`, `Draft changes after r1` | 확정된 revision 여부 |
| 외부 전달 | `Worksheets exported`, `WebDB uploaded r1`, `Partial upload` | 외부 출력 결과 |

r1 업로드 후 편집했다면 단순히 `Uploaded`라고 표시하지 않는다.

```text
Draft changes
Last upload: r1 · 23 Jul 2026
```

Finalize는 Plan revision을 고정한 상태이며, 실제 soaking이나 harvesting이 완료되었다는
의미로 사용하지 않는다.

## 11. 경고와 오류 표현

| 상황 | 표현과 동작 |
|---|---|
| 저장됨 | Status bar에 `✓ Saved locally` |
| 저장 전 변경 | `● Unsaved changes` |
| 저장 실패 | 사라지지 않는 `Save failed · Retry` |
| Calibration 경고 | 이미지 아래와 Summary 해당 행에 표시 |
| 여러 well 문제 | `3 wells need attention · Review` |
| RMServer offline | 이미지 영역에 `Images unavailable · Retry` |
| Worksheet 공유 위치 불통 | 대상 경로와 `Choose another location…` |
| 일부 WebDB 업로드 | 지속 경고와 업로드 이력 진입점 |

문제가 있는 selection을 Plan에서 조용히 제외하지 않는다. 오류가 있는 well과 position을
목록으로 보여주고 사용자가 직접 수정하도록 한다.

## 12. 키보드와 마우스 Interaction

| 입력 | 포커스 | 동작 |
|---|---|---|
| 왼쪽 클릭 | 이미지 | Soaking position 추가 |
| 오른쪽 클릭 | 이미지 | 기존 position 제거 |
| `←` / `→` | 이미지 | 이전/다음 이미지 |
| `↑` / `↓` | 이미지·Plate 목록 | 이전/다음 plate |
| `↑` / `↓` | Target Summary | 이전/다음 행과 이미지 |
| `Enter` | Well 입력 | 입력한 well로 이동 |
| `Esc` | Well 입력 | 입력 취소, 현재 well 복원 |
| `Esc` | 수동 calibration | Calibration 작업 취소 |
| `0` | 이미지 | Fit |
| `+` / `−` | 이미지 | 확대/축소 |
| `Ctrl/Cmd+L` | Image Review | Well 입력으로 이동 |
| `Ctrl/Cmd+Shift+T` | 작업 화면 | Target Summary 열기/닫기 |
| `Delete` | Summary | 선택한 position 삭제 |
| `?` | 텍스트 입력 외 | 단축키 안내 |

입력란에 포커스가 있을 때 방향키는 텍스트 편집에 사용한다. 이미지나 Plate 목록에
포커스가 있을 때만 탐색 단축키가 작동한다.

## 13. 처음 사용자와 단계적 정보 공개

Plate가 없는 초기 화면에는 다음 한 동작만 제시한다.

```text
No plates loaded
Load crystallization images to begin.
[Load Plates…]
```

첫 이미지에는 짧은 일회성 도움말을 표시한다.

```text
Click to place a soaking position. Right-click to remove.
← → images   ↑ ↓ plates
```

### 정보 공개 수준

| 항상 표시 | 상황에 따라 표시 | 상세 패널에 표시 |
|---|---|---|
| 현재 plate/well, 이미지 | Calibration 경고 | 중심·반지름 pixel 값 |
| 선택 well/position 수 | 저장 실패·offline | batch/profile 상세 |
| 저장 상태 | 수동 calibration 안내 | MxLive 연결 설정 |
| Project 이름·유형·revision | 재사용 이력 | 전체 파일 경로·기술 로그 |

## 14. 작은 화면과 큰 화면

설계 검증 기준은 최소 1280×800, 표준 1440×900, 대화면 1920×1080으로 한다.

작은 화면에서는 Plate 영역을 접을 수 있게 하고 Target Summary를 overlay로 표시한다.
Calibration 상세와 Plan settings는 필요할 때만 펼친다. 표는 가로 스크롤을 허용하되
plate와 well 식별 정보는 고정한다.

큰 화면에서는 Plate 영역과 Summary를 함께 표시하며 사용자가 dock 폭을 조절할 수
있게 한다. 최대화된 창에서는 Summary를 열고 닫을 때 전체 창 크기를 바꾸지 않는다.

## 15. Visual System

### 색상 출발점

| 용도 | 색상 예시 |
|---|---|
| 배경 | `#F4F6F8` |
| Panel·Table | `#FFFFFF` |
| 기본 문자 | `#202A35` |
| 보조 문자 | `#596575` |
| 경계선 | `#D8DEE6` |
| Focus·선택 | `#245FB5` |
| 정상 | `#246B49` |
| 확인 필요 | `#8A5700` |
| 오류 | `#B42332` |
| 이미지 주변 | `#171C22` |

본문은 OS 기본 UI font의 13–14px 상당, 표의 행 높이는 28–32px에서 실기기
검증을 시작한다. 숫자 열은 오른쪽 정렬하며 단위는 header 또는 일관된 suffix로
제공한다. 간격은 4/8/12/16px 체계를 사용한다. 그림자와 animation은 popup에만
제한적으로 사용한다.

주요 component는 Workspace selector, Plate list, Image canvas, Navigation bar,
Calibration inspector, Selection dock, Project list, Plan settings, Preview table,
Delivery bar와 Status bar다.

## 16. 접근성과 OS 일관성

- 키보드 focus를 명확한 테두리로 표시한다.
- 아이콘 버튼에는 tooltip과 accessible name을 제공한다.
- 상태는 색상뿐 아니라 문자, 기호와 선 형태로도 표현한다.
- OS font 확대와 High DPI에 대응한다.
- macOS에서는 `Cmd`, Linux에서는 `Ctrl` shortcut을 제공한다.
- Plate code와 well address는 좁은 영역에서도 생략하지 않는다.
- 중요한 정보가 tooltip에만 존재하지 않게 한다.
- Remote desktop 환경에서 hover와 animation에 의존하지 않는다.
- Summary에서 행을 이동해 이미지가 바뀌어도 keyboard focus는 Summary에 유지한다.

## 17. 대표 작업 흐름

1. Workspace를 열고 `Load Plates…`에서 2069와 2070을 입력한다.
2. 최신 batch/profile을 일괄 선택해 이미지를 불러온다.
3. 이미지에 soaking position을 지정한다. auto-next 기준보다 적게 선택해도 수동으로
   다음 이미지로 이동할 수 있다.
4. 선택 대상이 부족하면 `Reviewed, no targets` filter로 이전 이미지를 다시 본다.
5. Target Summary에서 방향키로 selection을 검토한다.
6. `New Project…`를 눌러 selected well 수를 확인하고 Plan type을 선택한다.
7. Summary와 worksheet preview를 확인하고 Plan을 Finalize한다.
8. Worksheet를 저장하고 WebDB preview를 검토한 후 MxLive로 업로드한다.

Soaking position을 하나 이상 지정하면 well이 선택되는 기존 원칙을 유지한다. Well을
선택하기 위한 별도의 checkbox나 추가 작업을 만들지 않는다.

## 18. 유지·통합·이동할 요소

### 항상 유지할 기능

- 이미지 중심의 검토 화면
- 빠른 이전/다음 이미지와 plate 이동
- Well address 직접 입력
- 왼쪽 클릭 추가, 오른쪽 클릭 제거
- `Targets/img` auto-next 기준
- Auto/manual calibration과 pixel-to-mm 변환
- 다중 plate와 batch/profile 고정
- Target Summary에서 이미지 이동과 삭제
- Project selection snapshot과 재사용 표시
- Raw Crystal과 Fragment Screening workflow
- Worksheet preview, Finalize, MxLive upload와 audit history

### 통합하거나 상세로 이동할 요소

- New/Rename Workspace → Workspace 메뉴
- Plate type/codes/batch/profile → `Load Plates…`
- Up/Down/Remove/Restore → Plate drag, keyboard, `⋯` 메뉴
- Calibration 버튼과 기술 수치 → 상태 한 줄과 `Adjust…`
- 여러 review 통계 문장 → Plate card와 Selection bar의 명시적 범위
- 파일 경로 → Status bar에서 한 줄로 elide, 상세 확인 가능

## 19. 적용 단계와 검증

| 단계 | 변경 범위 | 검증 항목 |
|---|---|---|
| 1 | 용어, 상태, 중복 정보 정리 | Saved/Finalized/Uploaded 구분 |
| 2 | Image Review 배치 변경 | 동일 입력에서 동일 좌표 저장 |
| 3 | Calibration 상세과 Summary 재설계 | Focus, overlay, zoom 좌표 |
| 4 | Planning Project header와 action bar | Snapshot과 live selection 구분 |
| 5 | Project 생성과 fragment 재배정 통합 | Fragment, volume, 순서 보존 |
| 6 | 작은 화면, Linux, remote 환경 조정 | Keyboard-only 작업 가능성 |

기존 selection 데이터와 worksheet 결과를 golden 기준으로 삼아, 같은 입력에서 같은
출력이 생성되는지 확인하며 UI를 교체한다.

## 20. 세 가지 구현 수준

| 안 | 범위 | 예상 작업량 | 회귀 위험 |
|---|---|---|---|
| A | 현재 PyQt 화면에서 문구, 간격, 상태 표현 정리 | Small–Medium | Low |
| B | PyQt를 유지하며 정보 구조, panel과 상태 표시 재설계 | Medium–Large | Medium |
| C | 비동기 이미지 service, cache, Undo와 장기 experiment tracking까지 재구축 | Large | High |

### 최종 권장

**B안을 권장한다.** 현재의 도메인 로직과 테스트 기반을 활용하면서도 실제 연구자가
하루 종일 사용하는 화면을 근본적으로 개선할 수 있다. 먼저 Image Review와 Planning의
고해상도 mockup을 만들고 다음 세 시나리오를 실제 사용자와 검증한다.

1. 100장의 이미지를 연속 검토한다.
2. Target Summary에서 10개의 selection을 다시 확인하고 수정한다.
3. 한 Project를 생성해 worksheet 저장과 MxLive 업로드까지 완료한다.

이 검증에서 조작 횟수, 실수, 되돌아간 횟수와 작업 시간을 기록한 후 PyQt 구현을
단계적으로 교체한다.

