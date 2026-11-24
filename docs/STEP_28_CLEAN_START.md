# STEP 28 · 구버전 정리와 빈 메모리에서 재시작

2026-11-24, 사용자 요청: 변경된 입력·경험 설계에 맞춰 이전 코드와 경험을 지우고 새로 수집한다.

## 1. 정리 결과

기준 commit `3959448ebcde94ee5148256fb2f1d088299149ee`에서 `codex/v12-clean-start` 브랜치를 만들었다. 과거 commit으로 되돌리지 않았고, 기존 원격 이력은 덮어쓰지 않았다. 원문 설계 문서의 과거 코드·결과 보존 지침보다 이번 명시적인 초기화 요청을 우선했다.

- 구버전 추적 파일 233개 제거: 코드 74개, 검사 66개, 문서 85개, 설정 7개, 초기 예제 장면 1개. 설정 하나는 현재 공통 이름으로 대체했다.
- 이전 실행·영상·경험·교훈·생성 코드 스킬·동결 자료 3,255개 제거: 6,804,047,365 bytes, 약 6.34 GiB.
- 삭제된 소스의 Python 캐시 108개와 이전 통합 작업용 복사본 34개도 제거했다.
- 어제 v1.2 개발 확인용으로 등록했던 2건을 포함해 이전 사례는 이관하지 않았다.
- 현재 기본 메모리 `runtime/memory/assembly_v12`: **등록 사례 0개 / 해결지침 0개 / collection 0개**.

삭제한 경로와 개수만 [삭제 목록](CLEAN_START_REMOVED_FILES.json)에 남겼다. 이전 경험 본문·이미지·벡터·생성 코드는 이 목록에 포함하지 않는다. 이전 추적 소스는 Git 이력에서 확인할 수 있지만, Git에서 제외됐던 로컬 실행·영상·경험 파일은 삭제됐다.

## 2. 유지한 것

현재 실행에 필요한 로봇·그리퍼 모델과 라이선스, Python 환경, CAD 원본·제품 입력 자료, 외부 분해 모듈 checkout, DINO 가중치와 출처, 로컬 설정은 유지했다. E: 드라이브와 원래 분해 프로젝트는 정리 대상으로 삼지 않았다.

`frame_assembly_skills.py`, `post_grasp.py`, `insertion_skills.py` 등은 옛 이름이지만 현재 v1.2 실행기의 실제 파지·운반·삽입·검증을 담당하므로 남겼다. 경험 초기화는 로봇을 움직이는 공통 함수까지 없애는 작업이 아니다.

이동·접촉·IK·모션 플래닝·검증의 주요 16개 공통 모듈이 기준 commit과 동일함을 확인했다. `insertion_skills.py`는 사용하지 않는 구 장면에서 상수 하나를 가져오던 연결만 제거했고, 상수 값과 물리 동작은 유지했다.

## 3. 현재 코드로 연결을 정리한 방법

| 위치 | 변경 이유 |
|---|---|
| `scripts/assembly_v12/action_contract.py` | 구 그래프에서 사용하던 행동 검사·복사 실패 재시도 조건·현재 관측 함수를 분리했다. 함수 동작은 유지했다. |
| `scripts/assembly_v12/responses.py` | 기존 SDK 호출·비용 장부·응답 검사를 현재 공유 이력 정책 아래로 옮겼다. 예전 독립 정책과 생성 코드 스킬 검색은 가져오지 않았다. |
| `context.py`, `execution.py` | 위 현재 모듈만 사용하며 구 LangGraph·경험 DB·코드 스킬 VM을 import하지 않는다. |
| `memory.py`, `entry.py` | 메모리 기본 경로를 `runtime/memory/assembly_v12`로 통일하고 `memory-status` 확인 명령을 추가했다. |
| `config/llm.astra-high.toml` | 기존의 승인 범위 설정값을 유지하며 제품 이름이 붙은 파일명을 공통 이름으로 바꿨다. 새 호출의 승인을 의미하지 않는다. |
| `README.md`, `PROJECT_PLAN.md`, 실행 안내 | 삭제된 run·스크립트 대신 현재 입력·빈 메모리·수집 흐름을 설명한다. |

현재 스킬은 미리 검증한 로봇 실행 함수다. 앞으로 쌓는 것은 실행 사건의 객관 기록과 근거가 있는 자연어 해결지침이다. LLM이 Python 파지 보정 코드를 생성해 VM에 등록하던 구 경로는 제거했다.

## 4. 실제 확인한 결과

### 자동 검사

전체 남은 검사 **73개 통과**(35.579초). 입력 계약, 공유 이력, 비용 불명 실패의 처리, 빈 메모리, 등록·검색 조건, 수집/평가 분리, 실제 MuJoCo의 TCP·중력·접촉 검사 등을 포함한다.

처음 한 검사에서 `numpy.int32`와 MuJoCo enum의 비교 순서에 따른 자료형 차이가 발생했다. `test_workcell.py`에서 둘 다 정수로 비교하도록 고쳤다. 확인하는 결합 종류와 대상, 물리 통과 기준은 변경하지 않았다. 테스트를 지우거나 허용 범위를 늘려 통과시키지 않았다.

별도 소스 검사에서 행동 계약·재시도 조건·SDK 비용 처리 함수가 이전 구현과 동일하고, 현재 Python 56개 파일의 문법이 유효함을 확인했다.

### 새 CAD → 실제 분해 검사 → MuJoCo 조립

새 개발 CAD `runtime/v12_inputs/clean_start_cell_20261124/product.json`을 생성했다. 바닥판과 기둥으로 구성되며, 사용자가 부품별 로봇 좌표나 registry를 작성하지 않은 입력이다.

| 항목 | 실제 결과 |
|---|---|
| 분해 연결 | 원본 CAD를 입력해 네이티브 ASAP/RedMax 검사, 한 연속 체인의 1/1 제거 완료 |
| 판단 출처 | 개발용 `scripted`, R1 역할 분리 + 빈 메모리 검색 |
| 첫 파지 | 명시적으로 주입한 z=-18mm 후보가 복사 검사 중 `UNEXPECTED_CONTACT`로 거절됨 |
| 복구 | 개발용 복구 정책이 수정 파지를 제안하고 재검사 후 주 시뮬레이션에서 집기 실행 |
| 최종 설치 | 같은 주 장면에서 기둥 1/1 설치, 그리퍼 해제와 최종 안정성 8개 조건 모두 통과 |
| 검색 | 요청 1회, 반환 0건: 이전 사례를 사용하지 않음 |
| 저장 | 사건 1개는 새 검증 run에만 기록. 지침을 생성하지 않아 등록은 pending이며 검색 메모리는 0건 유지 |
| 실제 API | 분해·조립 모두 0회, 유료 비용 0 |
| 시간 | 전체 약 147.2초, 그중 실행 약 72.3초, 영상 생성 약 33.8초 |
| 영상·증거 검사 | 17개 항목 통과: 설치 증거, 원본 체인, 상태·영상·실패 이미지 해시, 시간 연속성 등 |

주 장면에서 부품을 순간이동시키거나 붙여서 성공을 만들지 않았다. 실제 설치 증거와 LLM 판단 성능은 별개이며, 이번 결과는 **Astra의 조립·복구 성공을 뜻하지 않는다**.

실제 결과 위치:

- `runs/clean_start_checks/unittest.log`: 검사 73개 결과.
- `runs/clean_start_checks/source_audit.json`: 공통 코드 보존 검사.
- `runs/clean_start_checks/memory_status.json`: 등록 사례·지침 0건.
- `runs/clean_start_validation/report.html`: 새 검증 실행의 설명과 영상 링크.
- `runs/clean_start_validation/result.json`, `metrics.csv`: 실제 결과와 지표.
- `runs/clean_start_validation/events.jsonl`, `incidents.json`, `retrieval.jsonl`: 새 시도·실패·검색 기록.
- `runs/clean_start_validation/final_verification.json`, `artifact_audit.json`: 설치·증거 검사.
- `runs/clean_start_validation/media/execution.mp4`, `initial.png`, `final.png`: 주 실행 상태에서 생성한 영상·이미지.

위 기록은 이번 정리가 실행을 망가뜨리지 않았는지 확인한 **새 개발 검증 자료**다. 과거 자료를 남긴 것이 아니며, collection 메모리나 평가 결과로 계산하지 않는다. 생성 파일은 Git에서 제외한다.

## 5. 확인·재실행 명령

PowerShell에서:

```powershell
cd C:\dev\llm-robot-assembly
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly --exec runtime/linux-venv/bin/python -B scripts/run_assembly.py memory-status
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly --exec runtime/linux-venv/bin/python -B -m unittest discover -s tests -v
```

같은 CAD를 다시 물리 검사하려면 새 출력 폴더 이름을 사용한다. 이미 존재하는 run은 덮어쓰지 않는다.

```powershell
wsl -d ASAP-Ubuntu -u root --cd /mnt/c/dev/llm-robot-assembly --exec env MUJOCO_GL=egl runtime/linux-venv/bin/python -B scripts/run_assembly.py run runtime/v12_inputs/clean_start_cell_20261124/product.json --bridge-config config/disassembly_bridge.wsl.example.json --policy scripted --condition R1 --seconds 300 --first-grasp config/v12_development_rejected_grasp.json --family clean_start_validation --output runs/clean_start_validation_repeat
```

새 제품 사용법과 collection/evaluation 절차는 [현재 실행 안내](INTEGRATED_ASSEMBLY_V12.md)를 따른다. 첫 수집에서는 과거 해결 사례를 자동으로 가져오지 않는다. 실제로 해결된 같은 부품의 사건과 지침이 검사를 통과한 경우에만 등록된다.

## 6. 남은 제한

이번 작업은 초기화와 실행 회귀 검사다. 새 Astra 유료 호출, 실제 DINO 추론 검증, 10부품 전체 연속 성공, 평가 batch, 실물 로봇은 실행하지 않았다. 작은 개발 조립 한 건의 성공을 전체 제품 지원이나 연구용 경험 수집 완료로 확대하지 않는다.

현재 코드와 빈 저장소는 새 수집을 시작할 기반이 준비되어 있다. 실제 Astra 수집에는 키·모델·승인된 실행 상한이 필요하고, 이미지 검색 수집에는 선택 비전 의존성과 실제 특징 계산 검증이 필요하다. 실패했거나 근거 없는 지침은 사례로 등록하지 않는다.
