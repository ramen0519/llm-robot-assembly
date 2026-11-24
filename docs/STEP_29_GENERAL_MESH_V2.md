# STEP 29 — 일반 메시 조립 v2 통합 구현·실행 보고

작성: 2026-11-24. 저장소: `C:\dev\llm-robot-assembly`.

이 문서는 API 실행 전 시점의 보존된 보고서다. 이후 사용자가 키와 총 $15를 승인하여 수행한 실제 Astra 조립·5회 시범 수집·지침 생성/재사용 및 F: 저장소 전환은 [STEP 30](STEP_30_LIVE_ASTRA_V2.md)을 확인한다. 아래의 API 0회/키 부재 상태를 현재 상태로 해석하지 않는다.

## 1. 결론과 적용 버전

**일반 메시 처리와 파지·설치·복구·경험 재사용·UI를 구현했고, API 없는 실제 물리 시험까지 수행했다. 그러나 첨부 작업서의 최종 프로토타입 완료 판정은 아직 아니다.** 실제 Astra의 일반 형상 포함 10부품 이상 완주, Astra가 생성한 해결지침의 등록·재사용, 실제 5회 시범 수집은 `not_run`이다.

- 시작 HEAD: `cfe6c882fbb933c35c49b6544901beaaa0419a7a`, 시작 브랜치 `codex/v12-clean-start`, 시작 작업 트리 깨끗함.
- 적용 브랜치: `codex/general-mesh-v2`.
- 구현 커밋: `dd0bf4214b408eefce1d382e1ebdad0cd1a00c8a`.
- 과거 검토 커밋으로 reset하지 않았다. 기존 사각 프레임 코드·입력·실행·경험을 삭제하지 않았다. 현재 진입점은 v2, 과거 경로는 `scripts/run_assembly_v12.py`로 명시적으로 보존했다.
- 원 분해 저장소·사용자 원본 CAD·기존 결과는 수정하지 않았으며 E:와 실물 로봇 명령은 사용하지 않았다.
- 이 보고서는 이후 별도 문서 커밋으로 추가한다. 실행 코드와 해당 run의 정확한 파일 hash는 각 `code_versions.json` 및 `replay_bundle/bundle.json`에 있다. 이번 작업에서는 GitHub에 새 브랜치를 푸시하지 않았다.

확인된 핵심 결과는 **실제 2부품 조립**, **실패→복구→설치**, **경험 저장→다음 검색→파지값 재검증→실제 설치**, **비대칭 부품 경사 파지와 30° 회전 목표 설치**, **UI에서 실제 조립·영상 확인**이다. 이 중 정책은 모두 scripted이고, 개발 지침은 시험 코드가 실제 근거에서 구성했다. Astra 판단이나 Astra 지침 생성으로 집계하지 않는다.

## 2. 일반 형상 연결부와 주요 변경

| 연결부 | 구현과 이유 | 실제 확인 / 남은 범위 |
|---|---|---|
| 입력·원본 계약 | `entry.py`, `preparation.py`, `validation.py`: 단위·부품 인스턴스·원본 메시와 native 형상·선택 체인·잔여 베이스·목표 SE(3) 검사. OBJ에 적용된 배치를 중복 적용하지 않음 | 기존 실제 native export 통합 통과. mm/cm/inch·목표 회전·중복 변환 반례 검사 통과. 새 raw CAD의 native 실행은 WSL 무응답으로 미검증 |
| raw CAD 브리지 | 사각 종류 분기 대신 실제 높이·지지 면적에 기반한 후보 순위. 실제 native 검증이 성공해야 export 사용 | 설정·호출·10초 건강 확인·오류 보고 연결. 실제 배터리 raw 입력은 건강 확인 시간 초과로 `not_run` |
| 일반 충돌 자산 | `geometry.py`: 볼록체, 직교면의 정확한 분해, 검증된 일정 단면의 프리즘 분해, 나머지는 bounded CoACD. 원본 표면/빈 공간/체적 품질 검사 | 원통·관통 구멍·비대칭 자산 통과. 배터리 6개 복잡한 메시의 자산 생성은 각각 45초 제한 초과. 거친 bbox로 대체하지 않음 |
| 물성·장면·초기 배치 | `scene.py`, `preparation.py`: 실제 메시, 원본 체적/COM/관성, 고정 베이스 하나, 동적 부품, 안정 자세 후보, 완성 영역·로봇 영역 예약, N 기반 상태 | 13부품 준비/컴파일 통과. 예시 밀도·마찰·지그는 가정값. 초기 배치 후 정착 pose를 한 번만 보존 기준으로 확정 |
| 파지 계약 | `grasp.py`, `policy.py`, 프롬프트: 실제 표면점·법선·접촉 간격, 위/옆/경사 후보, 방향 다양성. 후보 ID 또는 part-local mm/xyzw/개방 폭. bbox 밖 TCP 허용 | 형상 후보·직접 pose·stale revision 검사 통과. 경사 파지 실제 실행. 원통 옆 파지의 시험 후보들은 IK에서 실패. Astra 실제 선택은 미실행 |
| 파지/설치 분리 | `skills.py`: copy에서 접근·닫기·들기와 설치 가능성을 분리. `passed/failed/not_run` 및 주 상태 불변 기록 | 실패 후보의 grasp failed / installation not_run, 정상 후보의 양쪽 passed, 이후 주 장면 실제 집기·설치 확인 |
| 운반·설치 | 최신 관측 파지 상대변환으로 native 부품 경로를 TCP로 변환하고 Mink/관절 경로·충돌을 재검사. 일반 접근은 실제 파지축과 지지 여유, 삽입은 정의된 축 사용 | 주 2부품 native 역경로 실행 및 일반 경사 fixture 설치 통과. 원본 부품 경로를 로봇 관절 경로로 사용하지 않음 |
| 검증·복구 | `verification.py`, `completion.py`, `execution.py`: 실제 접촉·들림·슬립·보존, 단계/출처, 제한된 재시도, 하나의 완료 프로파일 | 최종 501개 공통 창 검사 통과. 실제 낙하 후 내려놓기/재파지 전략, 나사·클립·전기 연결은 구현 범위 밖 |
| 경험·평가·UI | `memory.py`, `artifacts.py`, `trials.py`, `ui.py`: 사건과 지침 분리, 필터 후 DINO, 고정 snapshot/원본 누수 방지, 이동 가능한 증거, 동일 CLI의 로컬 UI | 실제 개발 경험 1건 등록·검색·재실행 통과. 연구 수집/평가는 키·승인 총예산 없이 시작하지 않음 |

기존 Mink IK, DAQP, RRT-Connect와 서보 제어를 재사용했다. 일반 방향에서 한 IK 초기값이 실패하는 문제를 줄이기 위해 **최대 4개 초기값, 공통 시간 제한 내** 탐색을 추가했다. 실제 로봇 상태를 초기값으로 옮기지 않고 계산용 상태에서만 검사한다. 관절 경로는 0.25° 이하 간격으로 조밀하게 검사하며 실제 물리는 2 ms로 실행한다. 이는 수학적인 연속 충돌 보장이 아닌 유한 해상도 검사다.

`pyproject.toml`, `uv.lock`에 형상·UI·영상·검색 의존성을 기록했다. 기본 viewer DLL이 Windows에서 차단되어도 headless 물리가 실행되도록 viewer를 GUI 실행 시에만 불러온다. 원본 로봇·그리퍼·third-party 라이선스는 유지했다.

## 3. F01–F16 적용 상태

`적용`은 코드 경로를 연결했다는 뜻이며 모든 실제 모델/제품에서 검증했다는 뜻이 아니다.

| ID | 상태 | 구현·증거와 검증 한계 |
|---|---|---|
| F01 | 적용·실제 확인 | 목표 접촉 쌍을 방향 없는 쌍으로 관리, 지지력 방향은 별도 계산. `v2_frame10_reference`: 9개 active 부품으로 동일 상태를 각각 검사해 모두 통과. 운반 중에는 목표 근접·단계 조건 필요 |
| F02 | 적용·계약 확인 | `fresh_hold()` 계획용 관측 변환과 `slip_reference` 최초 기준 분리. 보정 후 목표가 되돌아가지 않는 검사 통과. 실제 미끄러짐 복구 성공은 별도 미검증 |
| F03 | 적용·통합 확인 | 실제 시간 변화로 executed 확정 후 tool/event 생성. 개발 실패는 planner_candidate, executed=false. 다음 pick/insert는 live_simulation,true. UI도 같은 사건 읽음 |
| F04 | 적용·계약/실제 제한 확인 | 공통 Budget을 copy·물리 step·IK/RRT·API에 연결. count 후 남은 시간 재계산 검사 통과. 메시/인코더/영상에 유한 subprocess 제한. 취소는 협력적이며 실행 중 native 호출은 해당 제한까지 반환을 기다릴 수 있음 |
| F05 | 적용·오프라인 확인 | direct/batch split 검사, collection 검색 OFF, 평가 계획+고정 snapshot, source run/geometry/mesh-set 중복 차단. 실제 독립 평가 미실행 |
| F06 | 적용·오프라인 확인 | 코드/pyproject/lock/XML/mesh/TOML/third-party/패키지/native DLL/가중치/준비 자료 inventory. 변경 검출. 가중치·전처리·카메라 버전 구분 |
| F07 | 적용·통합 확인 | final 부품 전체 동일 창·profile·동작 검사·모델·원 목표 대조, 실패 state/PNG/sidecar/action/source 교차 검사. 잘못된 성공/최종 창 누락 거절. 복구 run audit passed |
| F08 | 적용·오프라인 확인 | 전역 요청/토큰/비용 장부와 제어 4N+4 분리. 제어11+지침2가 총36에 들어가는 반례 검사. 실제 API 예산 사용은 0 |
| F09 | 적용·차단 확인 | 실제 Astra 수집만 허용, 시범→등록→동결→평가 안내. 5회 계획·0회 started·not_run·비정상 종료 코드 확인. 본 수집은 미실행 |
| F10 | 적용·제한된 기능 확인 | 문제 코드·출처·단계·접촉 상대 역할·동작·그리퍼·개방 폭/접근 방향 조건 후 DINO. ID/사각 shape 필터 없음. 모든 제품의 복잡한 주변 간극을 의미적으로 일반화한 검색 효과는 미검증; 반환 지침은 현재 장면 재검증 필수 |
| F11 | 적용·계약 확인 | 행동·실제로 적용된 전체 파지값/단위·성공 event·실패/설치 증거 검사. 임의 생성 코드 없음. 실제 Astra 지침 생성은 미실행 |
| F12 | 적용·실제 입력 확인 | 실제 source/native 표면·단위·원본 목표·파일 hash 비교. 배터리 원본 좌표 대응과 선택 체인 확인. source/native 근접 계산은 mm에서 수행 후 m로 환산하여 수치 오차 개선; 허용 오차는 완화하지 않음 |
| F13 | 적용·13부품 확인 | 완성품 전체 AABB와 접근 여유 예약, 10개 배열 상한 제거, 명시적 자원 제한. 12개 자유 부품+베이스 준비/컴파일 통과. 13부품 전체 로봇 조립은 미실행 |
| F14 | 적용·실행 확인 | python_geometry / astra 선택·수정·직접 pose / scripted / development_injection / development_guidance_revalidation 구분. 이번 실제 정책은 모두 scripted |
| F15 | 적용·실행 확인 | 최초 1회와 추가 후보 구분. 복구 run 후보2·거절1·재시도1, 내부 Python 후보512를 별도 기록 |
| F16 | 적용·실제 재생 확인 | 경험의 독립 evidence에 MJB·실제 상태·원본/충돌 자산·이벤트·코드 hash 보존. 그 묶음만 지정한 재생 완료. 원 run을 지우거나 옮기지 않고 독립 사본에서 검증 |

## 4. 완료 프로파일과 실제 측정

논리 프로파일 hash: `c2a925ce7e111ae5c807bfeec155bcc4dc56f971fa016012c95d1e8b9d03c37c`. 내부 파일은 `config/completion.v1.2.json`, 버전은 첨부 v1.2다.

원 CAD 목표와 초기 고정 베이스 기준, XYZ 각 축 5 mm 이하, 상대 SO(3) 회전각 3° 이하, 최종 경질 관통 0.5 mm 이하, 원점 선속도 0.001 m/s 이하, 각속도 0.01 rad/s 이하, 실제 해제·비접촉·그리퍼 5 mm 이상 이격을 확인한다. 실행 중 경질 관통 1 mm 초과와 기존 패드 3 mm 제한도 별도로 실패 처리한다. 정착 1초 후 1초의 **501개 경계 샘플** 전체를 검사한다.

| 실제 주 실행 | XYZ 최대 오차 (mm, 축별) | 최대 회전 오차 | 최대 경질 관통 | 최소 그리퍼 이격 | 결과 |
|---|---|---|---|---|---|
| `v2_memory_reuse_physics_01` 기둥 | 0.02572 / 0.02794 / 0.10775 | 약 0.0000335° | 0.10776 mm | 58.718 mm | 공통 창 501 samples passed |
| `v2_general_oblique_04` 비대칭 부품 | 0.84866 / 0.02961 / 0.16775 | 약 0.39073° | 0.18675 mm | 50.550 mm | 공통 창 501 samples passed |

기둥의 실제 정의된 삽입 깊이 16 mm에 대한 오차는 **0.10775 mm**, 사전 수직축 밖 오차 **0.09745 mm**, 사전 회전 오차 약 **0.05228°**였다. 일반 비대칭 fixture는 명시된 직선 구멍/깊이가 없는 놓기 작업으로 추가 삽입 검사를 N/A와 사유로 기록했다. 필요한 삽입 검사를 생략하여 기둥을 통과시키지 않았다.

기준 완성 장면은 별도 모델에서 베이스만 고정하고 검사한다. `reference_invalid`는 조립 성공이 아니며 경험 등록 근거로 쓰지 않는다. 목표 이미지의 직접 배치 역시 시각화용으로 별도 표기한다.

## 5. 실제 실행과 V01–V08

아래 경로는 모두 저장소의 `runs/` 아래다. 원본 실행·실패 기록을 유지했다. 최종 정량 파일을 우선하고 초기 디버깅 시도의 성공률을 연구 결과로 합산하지 않는다.

| 기준 | 이번 결과 | 증거 |
|---|---|---|
| V01 사각 10부품 | **부분 확인**: 실제 CAD의 완성 기준 상태·접촉 방향 회귀 passed, 91개 기존/신규 검사 통과. 새 v2의 10부품 순차 조립 완주는 미실행 | `v2_frame10_reference/reference_result.json`, `reference_verification.json`, `v2_unittest_final.log` |
| V02 일반 형상 | 원통·관통 구멍·비대칭 실제 충돌 자산 passed. 원통은 물리 파지 접근 시험에 진입했으나 후보 IK 실패. 구멍의 소낙차 접촉 시험은 빈 공간 통과/벽 차단 passed | `v2_geometry_probe_04/geometry_probe.json`, `v2_general_cylinder_side_04/component_result.json`, `v2_hole_collision_settle/collision_result.json` |
| V03 경사 파지/목표 회전 | **실제 물리 passed**. 비대칭 메시를 경사 파지, 약 99 mm 들림, 30° 목표 회전을 유지해 설치·해제·후퇴·연속 검사·영상 완료 | `v2_general_oblique_04/component_result.json`, `resolved_grasp.json`, `final_verification.json`, `media/execution.mp4` |
| V04 일반 형상 포함 10+ Astra 완주 | **not_run**. 실제 Astra 키 부재, 새 native 입력 연결 미완료. 작은 scripted/기준 배치로 대체하지 않음 | `v2_collection_readiness/scope.json` |
| V05 11+ 준비 | **준비 passed**. 이름이 다른 12개 자유 부품+베이스, 회전 목표, 자동 배치·MuJoCo 컴파일 | `v2_capacity_13_parts/prepared.json`, `component_result.json` |
| V06 해결→등록→다음 검색/재검증 | **개발 경로 passed**, 실제 Astra 생성/재사용은 not_run. 의도한 개발 교란 후 실제 설치, 사건에서 구성한 개발 지침 1건 등록, 다음 실행 DINO 검색 및 실제 설치 | 아래 경험 절 참조 |
| V07 잘못된 성공 반례 | **계약 검사 passed**: 계속 잡힘·속도·관통·축 오차·회전·관찰 중간 이탈·불완전 창·필요 깊이/정렬 누락·audit 최종 창 누락 거절 | `tests/test_v2_completion.py`, `test_v2_contracts.py`, `v2_unittest_final.log`. 전부를 로봇에서 새로 유발한 물리 실패로 주장하지 않음 |
| V08 UI 사용자 흐름 | **scripted 실제 흐름 passed**. 입력→준비→실제 조립→physical_success/audit→영상 HTTP200. 키 없는 Astra 실행은 not_run으로 표시 | `v2_ui_physics.json`, `v2_ui_smoke.json`, 아래 UI 경로 |

검증된 native 2부품 입력은 `runs/clean_start_validation/input/export_v1.json`이다. 통합 실행 주요 ID:

- `v2_integrated_scripted_02-b0a9d424`: 정상 scripted 집기·설치·최종 검사·영상·audit passed.
- `v2_integrated_recovery_02-65bd884c`: 첫 개발 후보 거절 → recovery pick → assembly insert → physical_success/audit passed.
- `v2_memory_reuse_physics_01-f3c4ae6e`: 다음 검색의 파지값을 copy 및 주 실행에서 재검증 → physical_success/audit passed. 주 실행 3개 제어 판단, API 0.
- UI run `5da62551a99f49ec8257b1c2695ddcfd-337d9d33`: `runs/assembly_v2_ui/5da62551a99f49ec8257b1c2695ddcfd/`, physical_success/audit passed.

대표 영상:

- `runs/v2_memory_reuse_physics_01/media/execution.mp4`
- `runs/v2_general_oblique_04/media/execution.mp4` — 실제 상태 2,869개 중 716프레임을 선택한 영상, 물리 시간 143.106초.
- `runs/assembly_v2_ui/5da62551a99f49ec8257b1c2695ddcfd/media/execution.mp4`

소낙차 관통 구멍 시험은 500개 물리 step 동안 중심 탐침이 벽 접촉 없이 통과하고, 벽 위 탐침은 최대 0.22463 mm 관통으로 지지됐다. 이는 독립 충돌 시험이다. 선행 고속 구형 탐침 낙하는 약 2.214 mm 관통으로 실패했고 `v2_hole_collision_physics/`에 남겼다. 모든 속도·형상에서 접촉이 안정적이라고 주장하지 않는다.

초기 실패도 유지했다. `v2_integrated_recovery_01`은 물리 설치 후 Windows 경로 표기 때문에 audit가 중단됐다. v2 `Recorder.materialize()`에서 `/` 표기로 고친 뒤 새 run `_02`로 재검증했다. 옛 결과를 덮어써 성공으로 바꾸지 않았다. 일반 옆/경사 후보의 IK 실패는 선택한 후보·시간 한도의 실패이며 모든 파지의 불가능 증명이 아니다.

## 6. 경험·지침·검색의 실제 증거

실제 사건의 최초 실패는 `PLANNED_CONTACT`, phase `approach_grasp/preflight`, source `planner_candidate`, `executed=false`다. copy 검사에서 grasp failed, installation not_run, main_state_unchanged=true였다. 이후 실제 pick와 insert가 실행되고 최종 창까지 보존된 사건만 eligible로 만들었다.

- 실제 해결 사건: `runs/v2_integrated_recovery_02/incidents.json`의 `e045e42d80b170035a3f2764a068e07ef9b26a956817e7a96b1b71618368224f`.
- 등록 결과: `runs/v2_memory_roundtrip_01/memory_roundtrip.json` — registered 1, 실제 DINO embedding 1, 기존 실행의 snapshot 사례0 → 다음 snapshot 사례1.
- 사례 ID: `e842d4bb210e6ab8618d3cbacc64b6337376419e43978f6678dcd5414478d88e`.
- 보관 위치: `runs/v2_memory_roundtrip_01/isolated_development_memory/<사례ID>/archive.json` 및 `evidence/`.
- DINO: 실제 로컬 CPU 추출, 384차원, encoder version `a1c11fe02a39ee7aee769874b7642e8be6cf6af30ee44e5d11a13523bb6f9c2c`.
- 다음 실행 검색 receipt: `runs/v2_memory_reuse_physics_01/retrieval.jsonl`. 조건 필터 후 같은 실패 이미지의 score 1.0, `DINO_after_condition_filter`, 원본 실패 이미지를 모델에 추가 전송하지 않음.
- 실제 사용: 같은 run `events.jsonl`의 event2가 `development_guidance_revalidation`, 위 사례 ID를 참조. 최신 장면에서 다시 copy/집기/설치를 검사했다. event3에서 실제 설치 확인.
- 독립 재생: 사례 `evidence/replay_media_result.json`의 recorded_states_rendered, `evidence/media/replay.mp4`. 원 run 경로를 재생 명령에 주지 않았다.

**이 지침은 실제 Astra가 생성한 것이 아니다.** 개발용 검사 코드가 실제 적용값과 event ID를 인용해 만든 카드다. 별도 생성 Python 스킬도 아니다. 기본 연구 메모리 `runtime/memory/assembly_v2`에는 이 개발 카드를 넣지 않았다. 같은 입력·같은 이미지 재사용은 기능 확인이며 새 제품 검색 성능이나 개선율을 증명하지 않는다.

## 7. 실제 Framework 배터리 확인

사용자 프로젝트에 보존된 실제 Framework 배터리 자료를 우선 조사했다. `runtime/products/framework_battery_v1`의 원본/출처 자료를 읽고 `runs/v2_framework_battery_audit_02`에 새 전달 입력과 확인 결과를 만들었다.

- 실제 6부품, 고정 0, 선택 체인 **2→3→1→4→5**, 최종 잔여 **[0]**, 역순 후보 **5→4→1→3→2**, 각 단계 추가 지지 없음을 검증했다. 다른 분기 성공을 섞지 않았다.
- 제조사 원 STEP은 mm이고, 보존된 정규화 ASAP 단위는 단위당 **0.024 m**였다. STEP 원점 변환은 `[0, -6.81, 127.150464] mm`의 근거를 사용했다. 조립 좌표가 반영된 source OBJ와 부품 로컬 메시를 실제 표면으로 대조해 통과했다.
- 전달 입력: `runs/v2_framework_battery_audit_02/product/product.json`, 상세: `battery_check.json`.
- 이 옛 분해 결과는 부품 수준 증거다. 현재 strict export가 요구하는 dense path/check/media 묶음은 없어서 공통 브리지에서 재생성이 필요하다.
- `runs/v2_battery_native_bridge/result.json`: `/bin/echo` 건강 확인도 10초 내 응답하지 않아 not_run. WSL을 임의 재시작하지 않았다.
- 실제 메시 자산 생성은 5번을 먼저 시도하고 나머지도 검사했으나, 각 45초 한도에 걸렸다. 따라서 **배터리 5번 로봇 파지·조립도 not_run**이다. 구멍을 막는 bbox나 임의 접착으로 대신하지 않았다.
- 전기 연결·실제 배터리 안전 취급·나사 조임을 구현했다고 보고하지 않는다.

## 8. 사용량·시범/본 수집·미실행

이번 작업에서 실제 Astra 생성/토큰/API 비용은 **0회 / 0 / $0**다. mock API 계약 검사와 로컬 DINO 추출은 별도다. 다른 프로젝트의 유료 승인이나 예전 회차의 $1.50를 신규 5회 예산으로 확대하지 않았다.

`runs/v2_collection_readiness/`에 검증된 하나의 입력을 5회 반복하는 시범 계획을 만들고 실행 전 검사를 수행했다. 이는 다양한 제품의 본 수집 manifest를 대신하지 않는다.

- `pilot_plan.json`: 5개 계획, retrieval OFF, 실제 Astra 조건.
- `pilot_blocked/batch_result.json`: requested5/planned5/started0/completed0/registered0, status not_run, 종료 코드2.
- 명시된 차단: 현재 프로세스 API 키 없음, 승인된 **잔여 총예산** 파일 없음. 본 수집 대상 manifest도 설정되지 않음.
- 실제 5회 시범·본 수집·Astra 해결지침 생성·네 조건 성능 비교·경험 효과 결론은 모두 미실행이다.

실행 결과·영상·재생 사본 때문에 마지막 확인 시 C: 여유가 약 **0.94 GB**였다. 대규모 수집 전에는 저장 공간을 확보해야 한다. 사용자 자료나 실패 기록을 자동 삭제하지 않았고 E:를 사용하지 않았다.

## 9. 실행 방법과 바로 열어볼 결과

공통 설치·키·DINO·브리지·시범/본 수집·동결/평가·재생의 정확한 명령은 [운영 안내](ASSEMBLY_V2_OPERATIONS.md)에 있다.

현재 컴퓨터:

```powershell
cd C:\dev\llm-robot-assembly
$assemblyPython = '.\runtime\luna-venv\Scripts\python.exe'
& $assemblyPython -B scripts\run_assembly.py status
& $assemblyPython -B scripts\run_assembly.py run --input 'C:\path\product.step' --policy astra --condition R0 --seconds 600
& $assemblyPython -B scripts\run_assembly.py ui --port 8765
```

일반 UI 기본 주소는 http://127.0.0.1:8765 이고, 이번 마지막 검증 서버는 **http://127.0.0.1:8766** 에 실행해 두었다. 실제 scripted 조립 결과 페이지:

http://127.0.0.1:8766/files/5da62551a99f49ec8257b1c2695ddcfd/report.html

검색 지침 재검증을 API 없이 다시 실행하려면 새 output을 사용한다.

```powershell
& $assemblyPython -B scripts\run_assembly.py run --input runs\clean_start_validation\input\export_v1.json --policy scripted --condition R1 --first-grasp config\v2_development_rejected_grasp.json --memory-root runs\v2_memory_roundtrip_01\isolated_development_memory --seconds 600
```

현재 준비된 자료/메모리는 로컬 자료이며 Git에는 포함하지 않는다. 새 PC는 보존한 입력 패키지를 전달하거나 native bridge를 통해 준비해야 한다.

## 10. 남은 한계와 다음 통과 조건

1. **실제 Astra 미검증**: 키가 전달되는 실행 환경과 승인 잔여 총예산을 설정한 뒤 생성/역할 전환/지침 생성/현재 장면 재검증을 실제 API로 확인해야 한다.
2. **새 raw CAD 전체 연결 미검증**: WSL native 환경이 응답해야 한다. 이후 배터리의 전체 검증 export와 원본 경로를 다시 생성해야 한다.
3. **일반 형상 10+ 완주 미실행**: 작은 실제 경사 설치와 13부품 준비를 대체 증거로 세지 않았다. 실제 native 순서가 확보된 10+ 일반 조립품에서 V04를 수행해야 한다.
4. **복잡한 배터리 충돌 자산 미완성**: 현재 품질/시간 한도에서 적합한 자산을 얻지 못했다. 형상 분해 비용과 빈 공간 보존을 해결해야 실제 로봇 시험을 시작할 수 있다.
5. **파지·동작 탐색 한계**: 유한 표면 샘플과 후보 목록, IK 초기값·RRT 시간 제한이 있다. 자동 공급 배치의 기하 가능/정착/각 부품 로봇 접근은 별도 판정한다. 옆 파지 후보 실패를 물리적 불가능으로 일반화하지 않는다.
6. **검색 효용 미검증**: 현재 필터·DINO 기능은 실제 연결됐지만 복잡한 주변 간극의 의미 이해나 새 형상 일반화 성능을 입증하지 않았다. 독립 입력에서 수집/평가를 분리해야 한다.
7. **모델링 범위**: 닫힌 강체 표면·마찰 기반 파지·놓기/정의 가능한 삽입. 유연체·케이블·나사 토크·클립 잠김·접착·전기적 완료는 별도다. 충돌 품질은 유한 표본 검사이고 고속 충격 반례도 존재한다.

따라서 **오프라인 개발 경험의 수집·검색·재실행은 가능한 상태**이며 실제 사례로 확인했다. **연구용 Astra 경험 수집을 지금 바로 시작할 준비가 끝난 상태는 아니다.** 위 키·예산·native 입력·저장 공간과 5회 시범 검증을 충족해야 한다. 최종 프로토타입 전체 완료 또는 일반화 성능 검증 완료라고 보고하지 않는다.
