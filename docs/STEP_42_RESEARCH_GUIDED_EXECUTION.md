# STEP 42 · 관련 연구 대조와 실패 단계 분리

2026-11-25. 사용자 요청에 따라 관련 연구 14편과 공개 구현을 확인했다. [상세 비교](LLM_ASSEMBLY_RESEARCH_REVIEW_20261125.md).

## 결론

고수준 LLM + 로봇 실행기라는 구조는 유지한다. ASPIRE/Agent as Policy처럼 실행 프로그램을 수정하는 능력과 현재 우리 Astra의 제한된 행동/파지 제안 능력을 구분한다. 동작 코드 수정과 실제 Astra의 자율 학습을 섞지 않는다.

우선 문제는 파지와 설치 공간의 공동 검사, 명령한 벌림의 실측 일치, 고정된 들기/설치 경로, 시간 소진의 오해다. 같은 상황에서 재현되지 않는 지침을 대량으로 쌓는 것보다 기본 동작의 검증을 먼저 한다.

## 이번 연결

- `scripts/assembly_v2/failure_diagnosis.py`: 원 검출값을 유지하면서 실패 단계와 다음 확인 근거를 구별. 원인 확정·움직임 재시도·물리 성공을 선언하지 않는다.
- `scripts/assembly_v2/skills.py`: 실패 반환값에 진단 첨부. 체크포인트에 정렬/후퇴 상태도 남긴다.
- `scripts/continue_offline_install.py`: 실제 해제·후퇴 후 시간 소진만 제한적으로 이어가기. 모델/상태/설정 해시, 빈 손, 후퇴 기록을 확인하고 같은 완료 프로파일을 다시 적용한다.
- `scripts/review_assembly_failures.py`: 원본 실행을 읽기 전용으로 분석하고 분기별 표를 생성한다.
- 개발용 배치: 시간 소진을 나쁜 파지로 간주해 다음 후보를 무조건 실행하던 동작을 중단하도록 수정.

## 검증

`python -B -m unittest discover -s tests -p 'test_v2*.py'`: **92개 통과**. CLI 오류 메시지는 잘못된 시간 상한을 거절하는 기존 음성 검사에서 발생했으며 전체 검사는 성공했다.

실제 셀 5 조립 후속 검사: `offline-install-2bbffd23`, 34.438초, 추가 API 0회. 실제 설치·해제·후퇴 상태를 복원한 뒤 원래 1초·501샘플 완료 기준으로 부품 3/4/5/6/10을 함께 검사했다. 셀 5의 XYZ 최대 오차는 축당 0.094/0.042/0.106mm, 상대 회전 0.029°, 경질 관통 0.435mm였다.

셀 7을 포함한 최신 시도의 결과는 `F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01/SEQUENCE_STATUS.json`에 별도 기록한다. 시간 상한으로 끝난 회차는 성공/물리 불가능으로 바꾸지 않는다. 전체 12개 설치 완료나 새 Astra 전체 성공은 아직 주장하지 않는다.

## 실행

```powershell
cd C:/dev/llm-robot-assembly
$assemblyPython = './runtime/luna-venv/Scripts/python.exe'
& $assemblyPython -B scripts/review_assembly_failures.py `
  --root 'F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01' `
  --output 'F:/dev/llm-robot-assembly-data/research/NEW_FAILURE_REVIEW'
```

출력 폴더는 새 이름을 사용한다. `REVIEW.md`와 `review.json`이 생긴다. 이 명령은 API 호출과 새 물리 실행 없이 기존 결과만 읽는다.

## 남은 일

일반 접근·들기·운반·접촉 동작을 더 안정시키고, 행동 프로그램의 수정 범위를 넓힌 다음 실제 Astra로 재검증해야 한다. 지침의 별도 재현·다른 부품 전이·고정 메모리 평가도 아직 별도 과제다. 이번 추가 자료는 개발 근거이며 연구 경험으로 자동 등록하지 않았다. 새 유료 호출 0회, 실제 하드웨어 명령 0회.

ASPIRE 참고 소스는 F 드라이브 조사 폴더에 고정 커밋, LICENSE/NOTICE, 파일별 해시와 함께 보관했다. 외부 소스를 실행하거나 큰 종속성 환경을 설치하지 않았다.

## 최종 후속 진단 — 셀 7 운반 시간 소진

원래 `part07_install_continuation01`은 `offline-install-45004541`, 250.469초로 종료했다. 실제 시뮬레이션은 49.198초 진행했고, 그중 운반 계획 시간은 48.637초였다. 경로 뒤 정착 확인 도중 240초 계산 예산이 소진됐다. 이를 새 파지 실패나 전체 조립 불가능으로 판정하지 않았다. 원 결과와 영상은 보존했다.

`continue_offline_install.py --resume-stage transporting`은 실제 운반 단계의 시간 소진만 받는다. 완전 적분 상태·모델·준비 자료·제어 목표의 동일성을 확인하고, 원래 파지와 미끄럼 참조를 복원한다. 현재 접촉·파지 유지·50mm 이상 이격을 재관측한 뒤 현 위치에서 새 경로를 계산한다. 원래 실패한 움직임을 무조건 이어서 재생하지 않는다. 이는 별도 scripted 진단이며 Astra의 자동 복구 완료가 아니다.

후속 `part07_transport_continuation01`, run `offline-install-1508b00a`: `component_failed`, 250.500초. 실패/종료 단계 `retreat_limited_open`, 코드 `TimeoutError`, 이유 `deadline:physics_step`. 셀 7 완료는 미확인이다.

현재 검증된 개발 분기는 **5/12개**, 부품 `10, 3, 4, 5, 6`다. 전체 조립 완주·빈 장면부터 새 Astra 실행·새 경험 등록은 미수행이다. 이번 후속 API 0회 / $0. 기존 실험용 교체 손가락 모델을 유지했으며 기본 2F-85나 실물 장치의 성능으로 일반화하지 않는다.

전체 v2 회귀 검사: **95개 통과**, 실행 시간 4.559초. 제한된 파일 접근 환경의 첫 검사에서는 UI 의존 라이브러리 읽기 권한으로 두 모듈을 불러오지 못했다. 허용된 실행 환경에서 같은 전체 검사를 다시 실행해 95개를 모두 통과했다. 물리 시험은 위 결과로 따로 판단한다.

- 분기별 최종 검토표: `F:/dev/llm-robot-assembly-data/research/20261125_execution_review/failure_review02/REVIEW.md`
- 마지막 후속 영상: `F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01/part07_transport_continuation01/media/execution.mp4`
- 재개 직후 실제 관측: 같은 실행 폴더의 `resume_observation.json`
- 원본 상태/제어 참조: `source_checkpoint.json`, `source_evidence.json`

```powershell
cd C:/dev/llm-robot-assembly
$assemblyPython = './runtime/luna-venv/Scripts/python.exe'
& $assemblyPython -B scripts/continue_offline_install.py `
  --source-run 'F:/dev/llm-robot-assembly-data/runs/lab_battery_sequence_20261125_01/part07_install_continuation01' `
  --checkpoint 'checkpoints/offline-install-45004541_insert_stopped_diagnostic.json' `
  --resume-stage transporting `
  --output 'F:/dev/llm-robot-assembly-data/runs/NEW_TRANSPORT_DIAGNOSTIC' --seconds 240
```

위 명령은 자동으로 실행하지 않으며 동일 근거의 별도 재현용이다. 새 출력 폴더가 필요하다. 증거가 맞지 않으면 실행을 거절한다.
