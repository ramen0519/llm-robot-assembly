# 연구실 배터리팩: 대체 파지·손가락·공구 비교

2026-11-25. 사용자 요청에 따라 API 없는 자율 대안 시험을 수행했다. MuJoCo 시뮬레이션만 사용했고 실물 로봇은 움직이지 않았다.
시작 HEAD: `cf8add83cb2704c61ab4dc928119fb5276f1d33f`, branch `codex/general-mesh-v2`. 기존 변경을 보존했고 이 작업에서는 commit/push하지 않았다.

## 현재 결론

**기본 2F-85 본체와 링크는 유지하고 손끝을 길고 좁게 바꾼 강체 시뮬레이션에서 첫 홀더(3번)의 주 장면 조립에 성공했다. 전체 배터리팩은 12개 중 1개 설치이며, 완주한 결과가 아니다.**
집기·운반·목표 설치·제한 개방·후퇴·해제 후 안정성까지 독립 복사 장면과 주 장면에서 각각 통과했다. 다음 4번 부품은 복사 검사 중 1200초 회차 한도로 종료되어 조립 가능/불가능을 판정할 수 없다. 두 번째 회차 상태는 `deadline`이며, 3번의 개별 설치 성공 근거는 `VERIFIED_FIRST_PART.json`과 event 2에 남아 있다.
이번에는 유료 Astra를 호출하지 않고, CAD 기하 검사로 선택한 개발용 파지와 scripted 정책을 사용했다. 자동 에이전트 전체 제품 성공으로 해석하지 않는다.

## 결론을 읽을 때의 구분

- 단순 공간 검사: 해당 자세에서 기하가 겹치는지 보는 계산. 집기/설치 성공이 아니다.
- 끝점 IK 검사: 로봇 관절로 그 자세를 만들 수 있는지 검사. 이동 경로와 실제 접촉 동작은 별도다.
- 복사 장면 물리 시험: 모터·접촉·마찰로 실제 움직였지만 주 장면의 조립 실적과 구분한다.
- 주 장면 성공: 실제 설치·해제·후퇴 후 원래 완료 기준을 통과한 경우만 인정한다.

## 시도한 방법

| 방법 | 이번 시험에서 확인한 범위 |
|---|---|
| 기본 2F-85 파지 위치/방향 변경 | 바깥쪽 76개, 얇은 안쪽 부위 165개의 공간 검사에서 케이스 간섭 없는 후보를 찾지 못함. 전역 불가능성 증명은 아님. |
| 2F-85 본체를 유지한 긴 손가락 | 20/35/50/65/75 mm 연장, 폭 8/12 mm 및 끝부분 접촉 형상을 검사. 본체·링크·구동부는 유지. 강체 개념 모델로, 실제 제작품 검증은 아님. |
| 작은 평행 그리퍼로 교체하는 치수 검토 | 가상 본체 32×36×36 mm, 폭 6 mm, 손가락 도달 길이 110 mm. 실제 상품·장착·구동·팔 경로·파지 동역학 미검증. |

초기 긴 손가락의 명목 파지 공간 검사 19/165와 명목 IK 13개 통과는 실제 접촉 중심 보정 전 결과다. 이 수치를 조립 가능 개수로 해석하면 안 된다.
접촉 중심 보정 후 끝부분 접촉 형상은 케이스 공간 검사 11/165, 유한 접촉면을 고려한 명목 벌림 폭/IK 검사에서 우선 4개 후보를 골랐다. 실제 파지 확인은 아래 회차를 따른다.
소형 공구는 잡은 상태 1개, 2 mm 더 벌린 상태 23개가 각 공간 검사를 통과했다. 두 조건을 같은 후보가 모두 통과한 것은 1개다. 고정된 가상 공구의 공간 검사이며 조립 성공률이 아니다.

## 문제 → 원인 → 수정

1. 긴 손가락을 달아도 바깥쪽을 잡으면 케이스/기구부에 걸렸다. CAD의 안쪽 얇은 부위도 후보로 만들되, 최소 접촉 폭 1 mm는 공구 실험 설정에서만 허용했다. 기본 설정은 6 mm를 유지한다.
2. 새 손가락의 실제 접촉 중심과 후보 생성기의 기본 TCP 기준이 약 7.136 mm 달랐다. 실제 첫 닫기에서 패드가 홀더 위에서 닫혀 패드끼리 접촉했다. 이 실패를 보존하고, 빈 그리퍼 시뮬레이션에서 벌림 폭별 실제 접촉 중심을 측정해 후보 생성에 연결했다.
3. 손가락 끝의 4 mm만 접촉하도록 별도 형상을 만들었다. 최대 연장 75 mm에서 전체 길이 100 mm다. 케이스와 홀더 모두를 다시 검사한다. 손가락 재료·강성·체결·허용 힘/모멘트는 제작 검증하지 않았다.
4. 표면의 점 두 개 사이 거리만으로는 넓은 패드와 곡면의 접촉 폭을 알 수 없다. 유한한 패드 두께와 실제 벌림 상태로 명목 간섭을 확인하고, 최종 파지는 실제 닫기/인양이 판단하도록 유지했다.
5. 케이스 안에서 완전히 벌리는 대신 2 mm만 더 벌려 접촉이 사라지는지 확인하고, 충돌 검사한 경로로 나온 뒤 완전히 여는 선택 전략을 추가했다. 이 전략의 실제 통과 여부는 회차별 단계 기록으로 판단한다.
6. 많은 후보를 다양하게 고르는 계산이 반복되어 느렸다. 같은 선택 규칙을 유지하는 증분 거리 계산으로 바꾸고 매 반복에 시간 제한 검사를 넣었다. 원래 알고리즘과 선택 결과가 같은지 테스트했다.
7. 마지막으로 새 손가락인데 접촉 중심 보정이 없는 설정을 명확히 거절하도록 했다. 이 누락 차단은 물리 회차 종료 후 추가했으며, 실제 성공 입력의 후보/파지 해석이 동일함을 검사했다. 실행 당시 코드와 상태는 replay_bundle에 고정되어 있다.

## 실제 물리 회차

| run ID | 상태 | 복사 시험 | 주 장면 설치 | API |
|---|---|---|---:|---:|
| physical_ext75_01-d0865e77 | cancelled | 3: 복사 집기=failed, 설치=not_run; 3: 시간/중단으로 검사 미완료 | 0/12 | 0 |
| physical_end_contact_01-ea4ba94f | deadline | 3: 복사 집기=passed, 설치=passed; 3: 주 장면 설치/안정성 통과; 4: 시간/중단으로 검사 미완료 | 1/12 | 0 |

### physical_ext75_01-d0865e77

원래 목표 안정성: `passed`, 준비: `reference_and_initial_stability_passed`, 종료 사유: `TimeoutError: cancelled:graph_turn`.
- 부품 3, pick, 복사 파지 failed / 설치 not_run, 주 장면 실행 False.
  - UNEXPECTED_CONTACT, grasp_close: Unpermitted measured contact.; 상세 접촉은 comparison.json과 원본 events.jsonl 참조.
- 부품 3, pick, 복사 파지 failed / 설치 not_run, 주 장면 실행 False.
  - TimeoutError, approach_grasp: cancelled:physics_step; 상세 접촉은 comparison.json과 원본 events.jsonl 참조.
- 결과/실패 그림/주 장면 영상: `F:\dev\llm-robot-assembly-data\runs\lab_battery_tool_options_20261125_01\physical_ext75_01/report.html`, `failures/`, `media/execution.mp4`. 주 장면 영상에 복사 검사의 움직임을 섞지 않는다.

### physical_end_contact_01-ea4ba94f

원래 목표 안정성: `passed`, 준비: `reference_and_initial_stability_passed`, 종료 사유: `TimeoutError: deadline:graph_turn`.
- 부품 3, pick, 복사 파지 passed / 설치 passed, 주 장면 실행 True.
- 부품 3, insert, 복사 파지 None / 설치 None, 주 장면 실행 True.
- 부품 4, pick, 복사 파지 failed / 설치 not_run, 주 장면 실행 False.
  - TimeoutError, grasp_open: deadline:physics_step; 상세 접촉은 comparison.json과 원본 events.jsonl 참조.
- 결과/실패 그림/주 장면 영상: `F:\dev\llm-robot-assembly-data\runs\lab_battery_tool_options_20261125_01\physical_end_contact_01/report.html`, `failures/`, `media/execution.mp4`. 주 장면 영상에 복사 검사의 움직임을 섞지 않는다.

## 보존 및 검증

- 13부품 원본 형상 해시, 원래 CAD 목표, 월드 목표, 완료 프로파일을 비교해 유지됨을 확인했다. 이전 기준 회차의 7개 핵심 파일 해시도 동일하다(`preservation.json`).
- 기본 로봇/그리퍼 원본 XML과 메시를 수정하지 않았다. 선택한 실험 공구 설정이 있을 때만 메모리의 모델 구성에서 교체 가능한 패드를 바꾼다.
- 부품을 붙이는 weld/임의 부착/순간이동으로 성공시키지 않았다. 명목 계획 검사에서만 가상 위치를 사용하며, 실제 회차는 공급 위치에서 자유 강체와 접촉 마찰로 실행한다.
- XYZ 각 축 5 mm, 상대 회전 3°, 최종 경질 관통 0.5 mm, 해제 후 관측·속도 기준을 완화하지 않았다. 2 mm 두께의 새 패드는 관통 한도를 0.5 mm로 더 엄격하게 검사한다.
- v2 테스트 59개와 기존 TCP·IK·이동 회귀 검사 11개, 총 70개 통과. 기록 일관성 audit와 물리 성공은 서로 다른 판정이다.
- 이번 작업 API 호출 0회, 비용 $0. 이전 Astra 회차의 비용은 이번 작업에 합산하거나 새 승인으로 사용하지 않았다.

## 재실행

아래 입력은 원래 13부품이고, 공구 설정은 모든 제품에 적용되는 작업대 설정이다. --first-grasp는 이번 개발 검사를 위한 기하 후보이며 실제 Astra 판단으로 표시하지 않는다. 출력 폴더는 새 이름을 사용한다.

```powershell
cd C:\dev\llm-robot-assembly
$p = '.\runtime\luna-venv\Scripts\python.exe'
& $p -B scripts/run_assembly.py prepare --input 'F:/dev/llm-robot-assembly-data/inputs/lab_six_cell_battery_20261125_01/assembly_hint.json' --profile 'F:/dev/llm-robot-assembly-data/runs/lab_battery_tool_options_20261125_01/end_contact/workcell.json' --output 'F:/dev/llm-robot-assembly-data/runs/NEW_prepared' --seconds 300
& $p -B scripts/run_assembly.py run --input 'F:/dev/llm-robot-assembly-data/runs/NEW_prepared/prepared.json' --output 'F:/dev/llm-robot-assembly-data/runs/NEW_physics' --policy scripted --condition R0 --first-grasp 'F:/dev/llm-robot-assembly-data/runs/lab_battery_tool_options_20261125_01/end_contact/finite_pad_first_grasp_02.json' --seconds 1200
```

## 변경 파일

- `scripts/assembly_v2/tooling.py`: 명시적 실험 손가락 형상/질량/관성, 도구 해시에 묶인 접촉 중심 보정.
- `scripts/ur3e_with_gripper.py`, `scripts/workcell_scene.py`, `scripts/view_tcp.py`, `scripts/assembly_v2/scene.py`: 선택한 공구를 구성하고 TCP를 검증. 기본 2F-85 유지.
- `scripts/assembly_v2/grasp.py`: 도구 접촉 중심을 반영한 후보, 얇은 접촉 폭의 명시적 설정, 후보 다양성 계산의 반복 제거.
- `scripts/assembly_v2/installation.py`, `scripts/assembly_v2/skills.py`: 중력 반대 방향 접근 후보, 제한 벌림→후퇴→완전 개방. 매 경로/실제 접촉 검사 유지.
- `scripts/assembly_v2/verification.py`: 실험 패드의 엄격한 관통 한도.
- `tests/test_v2_tooling.py`: 기본 공구 보존, 형상 제한, 접촉 보정 출처, 후보 선택 규칙 회귀 검사.

## 남은 제한

- 이 시험은 실제 Astra 전체 자율 조립을 새로 실행한 결과가 아니다. 개발용 기하 후보와 scripted 정책을 사용했다.
- 첫 홀더 1개 이후 나머지 11개 설치, 새 공구의 실제 제작/강성/체결/허용 하중, 실물 로봇은 각각 별도 검증이 필요하다. 강체 손가락 모델에는 실제 금속의 휨이 반영되지 않았다.
- 소형 그리퍼 치수 연구를 상용 그리퍼 구매 권고나 실제 조립 성공으로 해석하지 않는다.
- 최종 성공이 없는 회차를 해결 경험/지침으로 등록하지 않는다. 실패 기록은 재검토 근거로 남긴다.
- 나사 체결·클립 잠금·전기 연결·실제 배터리 취급 성공을 주장하지 않는다.

## 첫 홀더 주 장면의 실제 측정값

| 항목 | 관측 구간 최악 값 | 기준 |
|---|---:|---:|
| X / Y / Z 위치 오차 | 0.041340 / 0.033641 / 1.000709 mm | 각 축 5 mm 이하 |
| 상대 회전각 | 0.038121° | 3° 이하 |
| 경질 형상 관통 | 0.434541 mm | 0.5 mm 이하 |
| 이동 속도 | 0.000002092 mm/s | 1 mm/s 이하 |
| 회전 속도 | 0.000000341 rad/s | 0.01 rad/s 이하 |
| 해제 후 관찰 | 연속 1초, 501개 경계 샘플 | 전 구간 통과 |

복사/주 장면의 최악 측정값은 동일했다. 같은 초기 상태의 결정적 재현 2회이며, 다양한 초기 조건에서의 통계적 성공률을 주장하지 않는다.
![저장된 주 장면에서 설치된 첫 홀더](F:/dev/llm-robot-assembly-data/runs/lab_battery_tool_options_20261125_01/observed_install_top.png)

원본 영상: `physical_end_contact_01/media/execution.mp4` — 실제 주 장면 108.642초, 20 Hz 저장 상태 2182개 중 5 Hz 영상 544프레임. 화면 보간이나 복사 시험 영상 합성 없음.
그림: `observed_lift_copy.png`는 복사 시험의 실제 인양 상태, `observed_install_top.png`는 주 장면의 설치 후 저장 상태다. 출처·모델·상태 해시는 각 JSON에 남겼다.

결과 화면: http://127.0.0.1:8768/results/40ee5af869989edf62eb2d52f360a574/report.html

주 장면 영상: http://127.0.0.1:8768/results/40ee5af869989edf62eb2d52f360a574/media/execution.mp4

로컬 UI의 결과/영상 HTTP 200을 확인했다.
