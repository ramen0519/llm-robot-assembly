# STEP 50 · CAD 하나의 입력에서 분해 검증·조립·경험 재사용·영상까지

## 실제 결과

2026-11-26, `prototype_20261126_raw_full01-13b61f4c`는 완성 배치가 든 CAD 목록을 한 번 입력한 실행이다. 고정 바닥 포함 3부품, 설치 대상 2개를 같은 MuJoCo 주 장면에서 모두 조립했다. `physical_success`, 기록 audit `passed`, archive 오류 없음이다. **판단은 scripted 개발 정책이며 실제 Astra 호출은 0회, 비용은 $0**이다.

이번에 입력한 것은 `F:/dev/llm-robot-assembly-data/research/20261126_prototype/socket_fixture/cad/assembly.json`이다. 단위 mm, 세 OBJ 대응과 완성 변환이 들어 있고 조립 순서는 없다. 제품별 registry나 실행 Python을 추가하지 않았다. 앞서 사용한 개발용 fixture와 같은 형상이므로 처음 보는 제품의 일반화 성능으로 주장하지 않는다. raw STEP 형식 자체의 새 검증은 아직 아니다.

## 실제로 이어진 흐름

1. 원본 CAD 목록 → native 형상 정리. 단위·원래 완성 변환과 출처 해시를 보존.
2. 기하로 바닥 `0`과 분해 후보 우선순위 생성 → 실제 ASAP/RedMax 경로·지지 검증 → 완료 체인 `2 → 1`, 최종 잔여 `0`.
3. 역순 `1 → 2`를 조립 후보로 전달. 부품의 분해 경로는 부품 SE(3) 자료이며 로봇 관절 경로로 사용하지 않음.
4. 실제 메시에서 충돌 자산, 공급 위치와 자세, 파지 후보, 설치 조건을 자동 준비. 완성 기준 장면도 별도로 검사.
5. 파지와 그 파지의 설치 가능성을 복사 장면에서 검사한 뒤, 같은 주 장면에서 로봇을 실제로 이동.
6. 첫 부품 설치 성공 → 두 번째 부품의 full-open 해제에서 이웃 부품 접촉 검출 → 격리 개발 메모리 검색 → 제한 개방·후퇴 계획 재검사 → 실제 주 장면 설치 성공.
7. 두 부품을 동시에 1초 연속 관찰 → 경험·지침 등록, 실제 상태 영상, 보고서와 UI 결과 확인.

전체 입력 처리·native 분해·조립·영상·저장까지 **539.063초(약 8분 59초)**다. 조립 실행기 내부의 기록 시간은 475.250초이며, 영상 생성 31.766초도 포함된다. 한 번의 기능 시험으로 전체 제품 성능이나 다른 제품의 소요 시간을 추정하지 않는다.

## 완료 기준의 실제 측정

두 부품의 최종 공동 관찰은 시뮬레이션 시각 185.556–186.556초, **1초·501샘플**이다. 원래 CAD 목표, timestep 0.002초와 기존 물리 기준을 유지했다.

| 항목 | 부품 1 | 부품 2 | 기준 |
|---|---:|---:|---:|
| XYZ 각 축 최대 오차(mm) | 0.02728 / 0.00865 / 0.10775 | 0.00540 / 0.00109 / 0.10775 | 각 축 5 |
| 최대 상대 회전각(도) | 0.000619 | 0.000680 | 3 |
| 최대 경질 관통(mm) | 0.10776 | 0.10776 | 0.5 |
| 최대 이동속도(mm/s) | 1.54e-11 | 1.56e-11 | 1 |
| 최대 회전속도(rad/s) | 4.23e-13 | 3.95e-13 | 0.01 |
| 삽입 깊이 오차(mm) | 0.10775 | 0.10775 | 1 미만 |
| 삽입 전 수직축 외 정렬 오차(mm) | 0.15191 | 0.11261 | 1 미만 |

이번 native 직선 경로와 원래 결합면 기하로 삽입 축·깊이가 정의되어 `linear_insertion` 검사도 적용했다. 이전 순서 hint만 사용한 실행과 구별된다. 해제·후퇴·로봇과 제품의 비접촉·그리퍼 여유 검사도 통과했다. 순간이동이나 인위적 부착으로 성공시키지 않았다.

## 검색과 해결 근거

- 후보 검사 3회, 거절 1회, 재시도 1회. 실패는 주 장면에서 충돌한 것이 아니라 설치까지 미리 시험한 복사 장면의 `UNEXPECTED_CONTACT / release`다.
- 검색된 기존 사례: `e3d5afb50150bcd88b9cb3e000d5f1883b8e2a26ba834885fee73569f35bcf25` (`prototype_20261126_multibody03`에서 생성).
- 문제 조건 필터 후 로컬 DINO 유사도 **0.9974169**로 선택. part ID는 기존 `taper_post`에서 이번 native ID `2`로 달라졌다. 같은 형상·같은 문제의 입력 전달 형식이 바뀐 기능 확인이다.
- 검색 지침의 실제 파지값과 `limited_open_then_retreat` 동작 계획을 복사 장면에서 다시 검사하고 주 장면에서 실행했다.
- `memory_use.json`: `executed_and_installation_preserved`, `current_scene_preflight=true`, `causal_benefit_proven=false`. 통계적 개선이나 Astra의 검색 판단으로 주장하지 않는다.
- 새 개발 해결 사례 1개 등록: `89ec5d4ac94d113e170e457a557733e13b0ca0554cc574f23ee28c17dd0e2d3d`. 실패 이미지 임베딩 1개. 다음 실행부터 사용 가능.
- 격리 메모리는 `F:/dev/llm-robot-assembly-data/runtime/memory/prototype_20261126_development`다. 연구용 실제 Astra 수집/평가로 옮기지 않았다.

## 확인할 파일과 명령

실행 폴더: `F:/dev/llm-robot-assembly-data/runs/prototype_20261126_raw_full01`.

- `disassembly/result.json`, `disassembly/export_v1.json`: 실제 분해 완료 체인과 역순 후보.
- `bridge_health.json`: 같은 native 환경의 연결 방식·출처 검사.
- `events.jsonl`, `retrieval.jsonl`, `memory_use.json`, `registrations.json`: 실패→검색→재검증→설치→등록.
- `final_verification.json`: 모든 완료 기준의 최대값·측정 창.
- `media/final.png`, `media/execution.mp4`: 실제 주 장면 결과. 영상은 저장된 실제 상태 933프레임·5fps로 만들었으며 새 물리 실행이 아니다.
- `report.html`, `audit.json`, `replay_bundle/`: 결과 및 출처 확인.

영상 SHA-256: `da26e4e72e8392ba70f8faab572e936d91842af17ef1cf11a34ebcd326041e84`.
MuJoCo 모델 SHA-256: `5978a661cbc155c1e0813267b4c9782ad5f785a583f047694baf5482e819efd6`.

```powershell
cd C:\dev\llm-robot-assembly
$assemblyPython = '.\runtime\luna-venv\Scripts\python.exe'

# 완성 CAD 목록만 입력하는 새 API 없는 기능 시험
& $assemblyPython -B scripts\run_assembly.py run --input 'F:\dev\llm-robot-assembly-data\research\20261126_prototype\socket_fixture\cad\assembly.json' --output 'F:\dev\llm-robot-assembly-data\runs\NEW_RAW_CAD_RUN' --policy scripted --condition R1 --development-learning --memory-root 'F:\dev\llm-robot-assembly-data\runtime\memory\prototype_20261126_development' --seconds 900

# 기존 결과를 감사하거나 실제 저장 상태를 영상으로 재생성
& $assemblyPython -B scripts\run_assembly.py audit --run 'F:\dev\llm-robot-assembly-data\runs\prototype_20261126_raw_full01'
& $assemblyPython -B scripts\run_assembly.py replay --run 'F:\dev\llm-robot-assembly-data\runs\prototype_20261126_raw_full01'
```

최신 UI를 키 없이 임시 8770 포트로 시작해 이 run의 목록 표시, 보고서·PNG·MP4 HTTP 200과 파일 형식을 확인했다. 브라우저 외형의 시각 검사는 아니다. 시험 서버는 종료했으며 증거는 `research/20261126_bridge/ui_result_check/result.json`에 있다.

## 실제 Astra와 남은 일

`research/20261126_bridge/astra_ui01/result.json`은 `not_run / User UI unavailable / api_calls=0`이다. 사용자가 키 입력 뒤 PowerShell 창을 닫아 서버와 일시 키가 종료된 상황을 확인했다. 현재 방식에서는 서버 창을 최소화한 채 유지해야 하며 브라우저만 닫아도 된다. 키 값을 저장하거나 보고서에 넣지 않았다.

키 연결 후에는 동일 입력의 실제 Astra high 판단·물리 조립을 별도 새 run으로 확인한다. 이번 단계의 scripted 성공을 그 결과로 대신하지 않는다. 배터리 전체 재시험, 실제 Astra의 새 해결 사례 등록/다음 실행 재사용, 독립 제품의 성능 비교는 이 결과에 포함하지 않는다.
