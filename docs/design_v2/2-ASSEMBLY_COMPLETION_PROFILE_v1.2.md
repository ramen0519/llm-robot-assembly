# 일반 형상 조립의 완료 판정 계약 v1.2

- 확정일: 2026-11-24, 한국 시간
- 함께 적용: `ASSEMBLY_CODEX_HANDOFF_v2.0.md`
- 상태: 구현할 설정/검증 계약. 현재 저장소에 적용·실행 완료했다는 뜻이 아니다.
- v1.1 대비: **위치·회전·관통·속도·시간의 확정 수치는 그대로 유지**한다. 사각 부품 전용 범위를 없애고, 공통 완료 검사와 동작별 추가 검사를 구분한다.
- 우선순위: 신규 실행에는 본 v1.2를 사용한다. 구 v1.0/v1.1과 과거 결과의 의미를 소급 변경하지 않는다.

## 1. 무엇을 완료로 판정하는가

원 CAD의 부품별 목표 위치·방향을 보존한다. 별도 완성 기준 상태가 물리적으로 유지되는지 검사하고, 실제 주 장면은 분리된 초기 배치에서 시작한다. 로봇이 설치 후 그리퍼를 놓고 물러난 뒤 **정착 1초+이어지는 연속 1초 관찰**로 목표 배치와 물리 유지를 검사한다.

부품별 설치 검사에는 현재 설치 부품과 기설치 부품을 포함한다. 전체 완료 검사에는 모든 필요한 부품이 같은 시간 창에서 조건을 만족해야 한다. 미설치 부품에 완성 pose 조건을 적용하지 않고 별도 초기 상태 보존/안전 정책을 유지한다.

판정 대상은 모델링된 강체의 기하 배치와 해제 후 유지다. 나사 토크·접착·전기 접속·클립 잠김 등 모델링하지 않은 기능 완료를 자동으로 증명하지 않는다. 실제 모델/동작의 부족을 보고하되, 곡면/오목함 자체를 실행 배제 조건으로 삼지 않는다.

## 2. 공통 수치

| 항목 | 확정값 | 정의 |
|---|---:|---|
| 위치 | XYZ 각 축 절댓값 ≤5mm | 고정 베이스 좌표계의 같은 부품 기준점. 세 조건 모두 만족 |
| 회전 | 상대 SO(3) 회전각 ≤3° | Euler 세 축에 각각 적용하지 않음 |
| 선속도 | 벡터 크기 ≤1mm/s | 부품 기준점의 속도로 일관되게 계산·기록 |
| 각속도 | 벡터 크기 ≤0.01rad/s | 약0.573°/s |
| 최종 경질 관통 | ≤0.5mm | 제품↔제품, 제품↔베이스/작업대의 실제 충돌 형상 |
| 실행 중 경질 관통 | >1mm이면 실패 | 접근·운반·설치·정착 전 과정에 적용. 위반 이력 보존 |
| 로봇–제품 접촉 | 최종 창 동안 없음 | 로봇이 제품을 받쳐 주는 상태를 완료로 인정하지 않음 |
| 그리퍼–제품 거리 | 최소5mm 이상 | TCP 거리 대신 실제 그리퍼 충돌 형상과 제품의 최소거리 |
| 기준 상태 | 정착1초+연속 관찰1초 | 주 장면과 분리한 완성 배치 검사 |
| 실제 설치 후 | 정착1초+연속 관찰1초 | 해제와 계획된 후퇴·최소 이격 확인 후 시작 |
| timestep/빈도 | 0.002초/매 물리 스텝 | 시뮬레이션 시간 기준, 저장/영상 빈도와 별개 |
| 중력 | [0,0,-9.81]m/s² | 단위 검증한 미터 모델 |

표의 이하/이상은 경계를 포함한다. 위치는 유클리드 거리5mm가 아니다. `(4,4,4)mm`는 위치 조건을 만족하고 `(5.1,0,0)mm`는 실패한다. 세 축이 각각5mm일 때 거리는 약8.66mm다. 거리 norm을 추가 성공 조건으로 조용히 적용하지 않는다.

고정 베이스 좌표계가 world와 회전되어 있으면 world XYZ 차이를 베이스 축으로 변환한다. 베이스가 이동한 만큼 원점을 다시 잡아 오차를 숨기지 않는다. 위치/속도 기준점, CAD/body/COM/bbox 변환을 명시해 같은 물리량을 비교한다.

회전은 `R_goal.T @ R_current`의 상대각 등 동등한 SO(3) 방식으로 계산한다. trace/quat 계산의 수치 범위와 xyzw↔엔진 규약을 처리한다. 대칭/동일 부품 교환을 이번 버전에서 임의 허용하지 않는다.

## 3. 동작별 추가 검사

### 3.1 검증된 직선 삽입 계약이 있는 경우

기존 사각 삽입 또는 새 일반 기하에서 **삽입 축·목표 깊이·기준 면을 자동으로 검증하여 정의한 동작**에는 다음을 유지한다.

- 최종 삽입 깊이 **오차** `<1mm`.
- 삽입 전 축에 수직인 평면 정렬 오차 `<1mm`, 상대 회전각 `<1°`.
- 해당 동작에서 실제 정의된 지지/접촉·도달·해제 조건.

입구에 걸린 채 목표보다4mm 덜 들어간 경우 공통 위치 기준은 만족할 수 있지만 깊이 오차 검사에서 실패해야 한다. 목표 회전을 맞췄다는 이유로 설치 깊이를 생략하지 않는다.

### 3.2 일반 목표 배치의 경우

가상의 구멍 폭·소켓 rim·깊이를 만들지 않는다. 일반 놓기/배치에는 공통 목표 pose·실제 형상/접촉·해제 후 유지가 기본 증거다. 삽입 전용 검사는 `not_applicable`과 적용하지 않는 이유를 저장한다.

선택한 동작에 필요한 검사를 계산하지 못한 경우는 `unknown/missing/incomplete`다. 이를 `not_applicable`이나 `passed`로 바꿔 성공시키지 않는다. 적용 조건과 검증 항목은 준비/행동 계약에서 자동 생성해 동결하고, 실행 실패를 본 뒤 유리하게 삭제하지 않는다.

분해 경로 길이 전체를 삽입 깊이로 사용하지 않는다. 명확한 축/깊이가 없는 곡선 경로는 그 실제 경로와 목표 pose를 검증하며, 알 수 없는 체결 품질까지 증명했다고 보고하지 않는다.

## 4. 접촉·시간 창·기준 상태

경질 제품 관통 한도는 연성 그리퍼 패드 접촉에 일괄 적용하지 않는다. 기존 로봇 충돌·파지 유지·접촉점 법선력 등 별도 동작 정책은 적용 대상을 명시하여 유지한다. 숫자를 근거 없이 완화하지 않는다.

목표 상태의 정당한 제품 접촉 쌍은 검사 순서에 무관하게 동일하게 처리한다. 실제 지지력 방향과 목표 pose·단계는 따로 확인한다. 목표 인접 쌍이라는 이유로 운반 중 모든 충돌을 허용하지 않는다.

첫1초 정착에는 최종 저속 조건을 즉시 요구하지 않지만 실행 중 충돌/관통 제한은 유지한다. 다음1초의 어느 표본에서든 필수 조건이 깨지면 해당 검사는 실패다. 자동으로 유리한 구간만 재선택하지 않는다. 복구 후 재검사는 새 check ID·창으로 남긴다.

실제 시작/끝 시간 차이가1초 이상이어야 한다. 2ms 간격에서 경계 표본을 모두 남기면501개가1초를 덮는다. 표본 개수만으로 시간을 대신하지 않는다. 표본 누락/기한 만료/예외로 창을 끝내지 못하면 incomplete이며 성공이 아니다.

별도 완성 기준 장면에는 실제 고정 베이스만 고정하고 다른 부품은 동적 강체로 둔다. 원 CAD 목표를 직접 배치한 기준 검사는 실제 로봇 조립 성공/경험으로 세지 않는다. 기준 실패는 `reference_invalid`와 관측 근거로 남긴다. 통과한 정착 pose로 목표를 덮어쓰지 않는다.

## 5. 구현할 설정 계약

아래 JSON은 설계 계약이며 현재 코드가 이미 읽고 있는 파일이라는 뜻은 아니다. 로더/저장 위치는 기존 프로젝트 구조에 맞추되 값·범위·버전의 의미를 지킨다.

```json
{
  "schema": "assembly_completion_profile/v1.2",
  "profile_id": "mesh_goal_axis5_release_stability_v1_2",
  "scope": "rigid_mesh_goal_assembly",
  "target_source": "original_cad_part_poses",
  "reference_settled_pose_replaces_target": false,
  "physics_timestep_s": 0.002,
  "gravity_m_s2": [0, 0, -9.81],
  "reference_settle_s": 1,
  "reference_observation_s": 1,
  "post_release_settle_s": 1,
  "post_release_observation_s": 1,
  "global_completion": {
    "position_error_metric": "componentwise_abs",
    "position_error_frame": "fixed_base",
    "position_error_max_xyz_m": [0.005, 0.005, 0.005],
    "position_error_comparison": "le",
    "orientation_error_metric": "relative_so3_angle",
    "orientation_error_max_rad": 0.05235987755982989,
    "linear_speed_reference": "declared_part_frame_origin",
    "linear_speed_max_m_s": 0.001,
    "angular_speed_max_rad_s": 0.01,
    "final_hard_contact_penetration_max_m": 0.0005,
    "gripper_product_min_clearance_m": 0.005,
    "robot_product_contact_allowed": false
  },
  "execution_hard_contact_penetration_max_m": 0.001,
  "operation_checks": {
    "linear_insertion": {
      "applies_when": "verified_insertion_axis_depth_and_reference_surfaces",
      "depth_error_max_m": 0.001,
      "depth_error_comparison": "lt",
      "prealignment_perpendicular_error_max_m": 0.001,
      "prealignment_orientation_error_max_rad": 0.017453292519943295,
      "prealignment_comparison": "lt"
    },
    "generic_goal_placement": {
      "requires_invented_socket_or_depth": false,
      "non_applicable_checks_require_reason": true
    }
  },
  "required_unknown_check_blocks_success": true,
  "installed_part_pose_preservation_uses_global_pose_limits": true,
  "uninstalled_part_preservation_policy": "separate_existing_policy",
  "compare_every_physics_step": true,
  "require_same_window_for_all_parts": true,
  "automatic_threshold_relaxation": false
}
```

## 6. 판정 결과와 소비자 일치

실행기·설치 상태 전이·경험 등록·audit·평가 동결·UI는 한 프로파일 원본과 같은 검사 결과를 사용한다. 최종/기설치 pose 보존에 옛 norm1mm/회전1°가 숨어 남지 않도록 수정한다. 미설치 보존·삽입 동작 조건은 별도 정책으로 유지한다.

각 검사 결과에 최소한 다음을 남긴다.

- profile ID/hash, source CAD/mesh/충돌 자산/모델 hash, 실행/검사/부품 ID.
- 적용 동작과 추가 검사의 정의·적용 근거·필수 여부.
- 기준 좌표계/기준점, CAD 목표/현재 pose, 부품별 XYZ 오차·상대각·속도 최댓값.
- 관통 최댓값과 실제 상대 geom/부품, 그리퍼 최소거리, 로봇 접촉 여부.
- 관찰 시작/끝·실제 길이·스텝 수, 조건별 상태·위반 시각/원인.
- copy/reference/main 출처, 주 장면 실제 실행 여부, 불완전 검사 원인.

검사 상태는 `passed/failed/not_applicable/not_run/unknown` 등 명확한 enum으로 관리한다. `not_applicable`은 false/true로 축약하지 않는다. 모든 required 검사가 실제 통과했고 같은 창을 완료했을 때만 해당 설치/전체 성공을 반환한다.

과거 `position_under_1mm`, `penetration_under_1mm` 등의 라벨에 새 값을 끼워 넣지 않는다. 버전 있는 의미와 수치를 기록하고 이전 경험/평가와 분리한다. 기록을 줄여도 매 스텝의 최악값·위반 사건은 보존한다.

## 7. 필요한 직접 확인

1. 축별 `(4,4,4)mm` 통과, `(5.1,0,0)mm` 실패, 경계5mm 포함과 회전된 베이스 좌표 확인.
2. 알려진 삽입에서 전역 pose가 통과해도 깊이 오차가 크면 실패. 일반 놓기에는 가짜 깊이 조건이 생기지 않음.
3. 계속 잡고 있는 상태·후퇴 미완료·과도 관통·관찰 창 중간 이탈·창 미완료 거절.
4. 기설치 부품 보존과 최종 전 부품 검사가 같은 프로파일을 사용.
5. 일반 비사각 부품의 실제 집기→운반→설치→해제→연속 관찰 결과가 경험/audit/UI에서 일치.
6. 원 CAD 목표와 실제 충돌 형상이 다르게 해석되지 않고, 기준 장면의 성공을 실제 조립으로 세지 않음.

공통 수치는 사용자와 정한 연구용 정책이다. 기존 사각 기준 상태에서 측정한 작은 오차가 새 일반 형상·충돌 근사·Astra 실행의 성능을 검증해 주지는 않는다. 실제 입력으로 다시 확인한다.

## 8. 근거와 적용 위치

검토한 코드에서 위치/삽입 검사는 [insertion_skills.py](https://github.com/ramen0519/llm-robot-assembly/blob/cfe6c882fbb933c35c49b6544901beaaa0419a7a/scripts/insertion_skills.py), 보존/접촉 검사는 [frame_assembly_skills.py](https://github.com/ramen0519/llm-robot-assembly/blob/cfe6c882fbb933c35c49b6544901beaaa0419a7a/scripts/frame_assembly_skills.py), 관찰 창은 [workcell_motion.py](https://github.com/ramen0519/llm-robot-assembly/blob/cfe6c882fbb933c35c49b6544901beaaa0419a7a/scripts/workcell_motion.py), 경험 등록은 [memory.py](https://github.com/ramen0519/llm-robot-assembly/blob/cfe6c882fbb933c35c49b6544901beaaa0419a7a/scripts/assembly_v12/memory.py)에 연결되어 있었다.

새 구현에서 파일/함수 경계를 정리할 수 있다. 중요한 것은 동일한 물리량·조건·창·증거를 모든 소비자가 해석하는 것이다. 최종 구현 범위와 기존 F01–F16은 동봉한 전체 작업서 v2.0을 따른다.
