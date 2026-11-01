# 23G: 10부품 연속 조립에 우회와 정렬 실패 복구 통합

작성: 2026-11-01. [23E](STEP_23E_FOUR_POST_DETOUR.md)의 네 기둥, [23F](STEP_23F_COMPOUND_TRANSPORT.md)의 슬리브·덮개 개별 시험 뒤 전체 실행기를 확장했다.

## 무엇을 완료했나

**고정 바닥판 1개 + 로봇 설치 9개**를 같은 MuJoCo 장면에서 조립했다. 기둥 4개·슬리브 4개·덮개 1개는 처음부터 작업대에 자유 물체로 놓인다. 바닥판 외에 미리 조립한 제품 부품은 없다. 그리퍼 접촉·마찰로 집고 운반·삽입·해제·후퇴하며 이전 조립물도 계속 검사한다.

`run_frame_assembly.py`의 선택형 우회 실행 범위를 2~5부품에서 2~10부품으로 확장했다. 모션 알고리즘·물리 모델·검사 임계값은 바꾸지 않았다. 허용 구간은 빈 그리퍼의 높은 접근과 부품을 든 자유 공간 운반이다. 접촉 하강 중에는 임의 RRT 우회를 하지 않는다.

성공 기준은 설치 9/9, 각 부품의 해제 후 결합 판정 통과, 같은 마지막 시점에서 전체 상태 검사 통과다. 통제된 고정 장애물이 처음부터 존재하고 첫 기둥에는 6mm 정렬 오류도 넣었다. 정렬 실패를 관측한 뒤 같은 파지를 다시 확인하고 재정렬했다. 실행 중 장면 초기화·부품 순간이동·인공 부착은 사용하지 않았다.

## 재현 명령

```powershell
cd C:\dev\llm-robot-assembly
uv run --locked python scripts/run_frame_assembly.py --parts 10 --mode single --scenario first_alignment_offset --transfer-obstacle --transport-strategy direct_then_rrt --snapshots
```

실행: `runs/step23_frame/20261101T081302_175687Z_3c24ac3a`. `--viewer`를 추가하면 같은 실행을 MuJoCo 창에서 볼 수 있다. 이번 통합 검증은 창 없는 계산과 결과 PNG로 수행했다. 기본 `direct`와 장애물 없는 기존 명령도 유지한다.

## 실제 결과

- 전체 **success**, 로봇 설치 **9/9**, `ten_part_assembly_complete=true`.
- 시뮬레이션 **957.474초**, 이 PC의 실행 시간 **1741.8초**. 다른 시험과 일부 CPU를 공유한 개발 기록이다.
- 물리 측정 **478,934행**, 9개 활성 부품 모두 기록. 시간 역행 없음, 연속 기록의 최대 시간 간격 **2.000ms**.
- 부품별 최종 검사 구간 최대 위치 오차 중 가장 큰 값 **0.1994mm**.
- 운반 중 최대 상대 미끄러짐 **1.739mm**. 이전 조립물의 목표 위치 대비 최대 오차 **0.1986mm**. 마지막 수치는 이전 위치에서 추가로 이동한 양과 다르다.
- 정렬 실패 **1회**, 초기화 없는 복구 **1회**. 직접 경로 사전 거부 **9회**는 실행 중 충돌 또는 조립 실패 횟수와 구분한다.
- 규칙 정책 호출 **29회**, 실제 API **0회**. 여기서 single은 단일 규칙 정책이다.

| 부품 | 최종 검사 최대 위치 오차(mm) | 빈 그리퍼 접근 | 부품 운반(재정렬 후 재접근 포함) |
|---|---:|---|---|
| post_1 | 0.1986 | checked_cartesian_waypoints | RRT-Connect, checked_cartesian_waypoints |
| post_2 | 0.1742 | RRT-Connect | RRT-Connect |
| post_3 | 0.1525 | checked_cartesian_waypoints | checked_cartesian_waypoints |
| post_4 | 0.1365 | checked_cartesian_waypoints | checked_cartesian_waypoints |
| sleeve_1 | 0.0915 | checked_cartesian_waypoints | checked_cartesian_waypoints |
| sleeve_2 | 0.0736 | checked_joint_interpolation | checked_joint_interpolation |
| sleeve_3 | 0.0542 | checked_joint_interpolation | checked_joint_interpolation |
| sleeve_4 | 0.1296 | checked_cartesian_waypoints | checked_joint_interpolation |
| cover | 0.1994 | checked_cartesian_waypoints | RRT-Connect |

계획 방법별 실행 수: `checked_cartesian_waypoints` 10회, `RRT-Connect` 4회, `checked_joint_interpolation` 5회. `checked_cartesian_waypoints`는 검사된 TCP 중간 경로, `checked_joint_interpolation`은 검사된 관절 보간, `RRT-Connect`는 실제 우회 탐색이다. 대체 경로 옵션을 켰다고 모든 이동이 RRT는 아니다.

## 동일 조건의 직접 경로 비교군

```powershell
uv run --locked python scripts/run_frame_assembly.py --parts 10 --mode single --scenario first_alignment_offset --transfer-obstacle --transport-strategy direct
uv run --locked python scripts/compare_transport_runs.py --direct-run runs/step23_frame/20261101T081637_610192Z_d40ea38b --fallback-run runs/step23_frame/20261101T081302_175687Z_3c24ac3a
```

직접 경로 실행은 sim 31.494초에 첫 운반 사전 검사에서 `PLANNED_CONTACT`로 중단했고 로봇 설치는 0/9다. 결과를 덮어쓰지 않았다. 설정과 기록된 코드 지문은 운반 전략 외에 일치했다. 비교: `runs/step25_transport/20261101T084224_970377Z_d99d6344`. 부분 실패나 종료 시간을 성공률·속도 우위로 바꾸어 해석하지 않는다. 고정 개발 사례 한 쌍이며 일반화 평가가 아니다.

## 검증과 복구 절차 등록

전체 실행기 입력 범위와 초기 설치 집계를 검사했다. 10부품 모두 초기 작업대 위치에서 시작하며 시작 직후 중단하면 설치 0/9다. `test_frame_detour.py -k full_frame_detour_scope` 1개 PASS, 0.193초. 로그 `.scratch/step23g_scope.log`. 실제 종단 검증은 위의 9개 설치 실행과 직접 경로 중단 실행이다. 전체 저장소 테스트를 다시 실행했다고 주장하지 않는다.

23F의 실제 복합 부품 파지·벽 충돌 검출·물리 상태 보존 검사 2개와 관련 장면 검사 4개도 통과했다. 평가 집계기의 합성 계약 검사 4개는 별도 [25B](STEP_25B_TRANSPORT_COMPARISON.md)에 기록했다. 이 합성 로그는 물리 성공 증거가 아니다.

최종 소스 추가 뒤 기존 첫 기둥 정렬 복구 절차를 새 물리 episode에서 재검증했다. `runs/step22_recipes/20261101T081744_399050Z_34a811af`, success, 현재 호환 등록본 1개. ID `0739ec88cef4dc0c821bcd8448d628418838fb32780fab9b800d280a58c517c1`. 장애물 없는 기존 첫 기둥 복구 조건에만 유효하다. 이번 10부품 우회를 새 스킬로 자동 학습·등록한 것은 아니다.

## 남은 범위

이 제품·초기 배치·고정 시험 장애물·seed 3에서 확인한 통합 결과다. 임의 제품·장애물·파지로 일반화하거나 연속 충돌 안전성을 증명하지 않는다. RRT 경로 중간에 부품이 기울어질 수 있으며 삽입 전 정렬을 별도로 확인한다.

실제 LLM 정책 호출, 대안 선택 효과, 외부 분해 플래너가 우리 CAD의 계획을 생성해 연결하는 작업, 동결한 스킬·메모리의 별도 평가가 남아 있다. 실물 작업은 이번 범위에서 제외한다.
