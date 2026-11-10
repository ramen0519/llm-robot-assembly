# 22I — 교훈에서 Python 스킬 후보 생성·저장

작성: 2026-11-10. 범위: 저장 Lesson → 실제 Luna 코드 생성 → 정적 검사 → 근거·조건과 함께 후보 저장.

**후보 생성 모듈을 구현했고 Luna 생성 1회로 후보 1개를 저장했다. 생성 Python 실행, MuJoCo 검증, 등록 스킬 선택은 아직 수행하지 않았다.** 기존 21N의 조립 성공을 이 코드의 성공으로 계산하지 않는다.

## 1. 이번 단계가 하는 일

Memory는 과거의 사건 기록, Lesson은 그 사건을 설명하는 조건부 가설이다. 이번 후보 코드는 그 가설을 다음 작업에서 계산에 사용할 수 있는 Python 함수로 표현한다. 사람이 일반적인 검사기와 입력·출력 형식을 정하고, 실제 후보 함수의 내용과 수정 전략은 Luna가 작성한다. 파인튜닝은 없다.

입력은 이전 단계에서 저장한 Lesson JSON 한 개다. 이미 Lesson 안에 검색 결과와 원래 사건 기록으로 가는 연결이 있으므로 이 단계에서 검색을 새로 만들거나 원래 교훈을 덮어쓰지 않는다.

1. Lesson ID, 파일 내용 hash, 연결된 경험과 적용 조건을 확인한다.
2. 교훈 설명, 실패·수정 시도 6개, 허용 함수 형식을 Luna에 전달한다.
3. Luna가 Python 코드, 근거 Lesson/사실 ID, 적용·제외 설명과 한계를 제출한다. 근거가 부족하면 기권할 수 있다.
4. 근거 ID와 Python 문법·형식을 검사한다. 원본이 바뀌었으면 저장하지 않는다.
5. 코드를 포함한 불변 후보 JSON을 별도 저장한다. 사람이 읽을 `.py` 사본도 남긴다.

**통과 기준:** 실제 저장 Lesson에서 생성된 코드가 원래 근거로 추적 가능하고, 적용 범위를 확대하지 않은 후보로 저장되며, 허용하지 않은 코드/조작된 근거가 거부되는 것. 로봇 조립 성공은 이번 통과 기준에 포함하지 않는다.

## 2. 코드가 받는 정보와 돌려주는 값

함수 형태는 `def propose(ctx):`다. `ctx`는 앞으로 실행 연결부가 현재 관측에서 만들어 줄 자료이며, 과거 Lesson의 설정을 현재 관측으로 재사용하지 않는다. 이번 단계에서는 이 인터페이스를 정의하고 코드만 생성했다.

| 입력 항목 | 뜻 |
|---|---|
| `part_size_xyz_mm` | 현재 기둥의 전체 X/Y/Z 치수, mm |
| `previous_grasp` | 앞서 거부된 파지의 x/y/z 및 손가락 닫힘 각도 |
| `failure_code`, `failure_phase` | 관측된 실패 종류와 발생 단계 |
| `holding_part` | 현재 부품을 들고 있는지 여부 |
| `observation_is_current` | 실행 연결부에서 확인한 관측 최신성 |
| `remaining_proposals` | 실행 연결부가 관리하는 남은 제안 횟수 |

반환값은 정확히 `x_mm`, `y_mm`, `z_mm`, `jaw_yaw_deg`를 가진 파지 제안 또는 `None`(보류)이다. 위치는 부품 중심 기준 mm, 각도는 부품 XY 평면의 손가락 닫힘 축 방위각이며 위쪽 접근만 대상으로 한다. 위치의 형식상 한계는 현재 부품 전체 치수의 절반, 각도는 ±180°다. 이 범위 안이라는 사실만으로 충돌 없음이나 성공이 보장되지 않는다.

`proposal_contract.host_action = propose_post_grasp`는 후보의 의미를 나타내는 이름이다. 기존 그래프에 새 동작이 등록된 것이 아니다. 후속 adapter가 검증된 네 수치를 기존 `pick` 제안의 `grasp`에 연결하고 현재 part/target/revision을 붙여야 한다.

후보는 MuJoCo 객체나 모터, `pick()` 호출을 받지 않는다. 입력 형식, 유한 숫자, 현재 관측·상태, 빈손, 적용 범위, 현재 형상 범위, 사전검사는 실행 연결부와 기존 검사기가 별도로 확인해야 한다. 함수가 `None`이나 네 수치를 반환한다는 실제 계산 검증도 후속이다.

## 3. 실제 Luna 생성 결과

- 근거 Lesson: `60e35a84545589bd6752c2f4e20040278b5a861709bf1aad31e65374d1337393`.
- 후보: `96fb203da360bcc6ca2b876d25a5206986fcf1fc44281003ad6a039026003bee`.
- 제목: `PLANNED_CONTACT 후 제한적 z 상향 후보`.
- 코드 SHA256: `92418c389b6d2a178cbfffe978a84babd93b835ccb1cc3b29d4e551ca371b2a8`.
- 지지 근거: F2/F4/F6. 검토했다고 제출한 근거: F1~F6 전체. 원래 실패와 수정 시도의 연결은 후보 안의 Lesson 사본에 보존된다.

소스를 읽으면 다음 논리를 작성한 것을 확인할 수 있다. 아직 이 함수를 실행해 반환값을 측정한 결과는 아니다.

1. 최신 관측이 아니거나, 부품을 들고 있거나, 제안 횟수가 소진됐으면 보류한다.
2. 실패가 `PLANNED_CONTACT / insert_lower/preflight`가 아니면 보류한다.
3. 현재 기둥 치수와 이전 파지를 읽는다.
4. 이전 위치가 부품 범위를 벗어났거나 1 mm 높일 여지가 없으면 보류한다.
5. X/Y와 닫힘 각도는 유지하고 Z에 1 mm를 더한 후보를 반환하도록 작성했다.

**1 mm는 Luna가 새로 제안한 미검증 시험값이다.** 과거 세 회차는 각각 Z=0 거절 후 +20/+4/+12 mm에서 기록된 삽입 측정을 통과했다. 이 사실이 +1 mm의 성공, 임계값, 보간 가능성을 입증하지 않는다. 생성 설명에도 이를 `UNVALIDATED` 휴리스틱으로 명시했다. 현재 코드에는 yaw 자체의 범위 검사가 없으므로 후속 호스트 출력 검사도 필수다.

모델 설명은 원문 그대로 보존했다. 적용 설명에 다른 언어의 단어 하나가 섞였고, 기록 장면과 현재 장면의 동일성 설명은 정밀하지 않다. C1/C2의 설정 일치와 전체 동적 상태의 동일성은 다른 문제다. 이러한 자연어를 실행 조건으로 자동 해석하지 않는다. 기계적인 적용 범위는 Lesson의 `observed_cases`를 그대로 상속하며, 원문 의미의 정확성·인과관계를 정적 검사로 보증하지 않는다.

## 4. 저장 내용과 코드 경계

| 파일 | 역할 |
|---|---|
| `scripts/grasp_skill_candidates.py` | Lesson 읽기·근거 검사, 생성 입력/출력, Python 정적 검사, 후보 생성·불변 저장 |
| `scripts/generate_grasp_skill_candidate.py` | 미리보기 또는 예산 있는 생성 1회, 요청·응답·비용·검토용 코드 기록 |
| `tests/test_grasp_skill_candidates.py` | 원본·조건·코드·저장 계약 검사 26개 |
| `tests/test_generate_grasp_skill_candidate.py` | 생성·실패·정산·미리보기 경로 검사 8개 |

후보 JSON에는 코드 원문과 hash, Lesson 전체 사본과 파일 hash, 근거 사실 ID, 적용 조건, 함수 계약, 생성 모델·요청·응답 hash, 생성 당시 구현 hash, 정적 검사 결과가 들어간다. 같은 후보를 다시 저장하면 기존 파일을 재사용하고 덮어쓰지 않는다. 생성 당시 구현 hash는 역사 자료로 보존하므로 단순한 후속 소스 변경으로 옛 후보를 변조로 단정하지 않는다. hash는 변경 탐지용이지 생성자 서명이나 진실성 증명이 아니다.

후보 상태는 `candidate_static_checked`다. `code_executed`, `execution_authorized`, `automatic_skill_registration`, `new_physics_validation`은 모두 false다. 등록 스킬 저장소와 별개인 `runtime/skill_candidates/simulation/post_grasp_v1`에 저장한다. 실험 데이터와 생성물은 기존 `.gitignore`의 `runs/`, `runtime/` 규칙으로 Git에서 제외된다.

정적 검사는 Python의 AST, 즉 코드의 문법 구조를 읽는다. 함수 하나, 지역 변수, 조건문, 사칙연산, 제한된 `min/max/abs`, 자료 읽기와 반환만 허용한다. import, 반복문, 입력 변경, 속성 접근, 다른 함수 정의와 임의 호출을 거부한다. **정적 검사는 실행 격리 장치나 계산 정확성 증명이 아니다.** 예를 들어 허용된 식이라도 실행 중 0으로 나누는 문제는 별도 실행 검사에서 다뤄야 한다. 생성 코드를 `exec`·import·일반 Python 프로세스로 실행하지 않았다. 분해 프로젝트의 VM 코드는 이번에 복사하지 않았다.

## 5. 실행 방법

현재 로컬 프로젝트에는 원본 경험과 Lesson이 존재한다. 새로운 clone에는 Git에서 제외한 이 자료가 없으므로, 먼저 22F~22H 과정을 수행하거나 출처를 보존한 로컬 자료를 준비해야 한다.

API 호출 없는 미리보기:

```powershell
cd C:\dev\llm-robot-assembly
.\runtime\luna-venv\Scripts\python.exe -B -X utf8 scripts/generate_grasp_skill_candidate.py --lesson runtime/lessons/simulation/post_grasp_v1/60e35a84545589bd6752c2f4e20040278b5a861709bf1aad31e65374d1337393.json --preview
```

예상: `status: preview`, `API_calls: 0`. 명시적인 LLM 설정을 생략해도 미리보기다. 교훈·근거를 확인하고 입력/요청을 별도 실행 폴더에 남긴다. API 키는 읽지 않는다.

실제 생성은 기존 키가 프로세스의 `OPENAI_API_KEY`에 준비된 상태에서 다음과 같이 실행한다. 설정은 이번에 사용한 로컬 예산 파일이며 새 clone에 포함되지 않는다.

```powershell
.\runtime\luna-venv\Scripts\python.exe -B -X utf8 scripts/generate_grasp_skill_candidate.py --lesson runtime/lessons/simulation/post_grasp_v1/60e35a84545589bd6752c2f4e20040278b5a861709bf1aad31e65374d1337393.json --llm-config runs/step22h-budget.toml
```

예상 가능한 상태는 `candidate_saved`, `abstained`, `failed`다. 새 호출이 항상 같은 코드를 만드는 것은 아니다. 오류는 자동 재시도하지 않는다. 후보를 만든 뒤 결과가 형식에 맞지 않아도 사용량을 먼저 정산하며, 사용량이 불명확하면 예약 비용을 유지한다. 한 실행의 제한은 기존 설정보다 늘리지 않고 생성 1회·최대 $0.01로 제한한다. 이 제한은 전체 프로젝트의 누적 예산 관리 자체를 대신하지 않는다.

## 6. 이번에 실제로 확인한 결과

| 검사 | 결과 |
|---|---|
| 새 후보 모듈·runner 검사 | 34/34 통과, 최종 repo 검사 11.896초 |
| 기존 교훈 검사 | 35/35 통과 |
| 기존 검색 검사 | 22/22 통과 |
| 기존 경험 변환 검사 | 29/29 통과 |
| 실제 생성 | `gpt-6-luna` 요청·응답, 후보 1개 저장 |
| API 요청 | 생성 1회 + 입력 토큰 계수 1회 = HTTP 2회 |
| 사용량 | 입력 4,802 / 출력 913 / 총 5,715 토큰 |
| 비용 | 설정 단가 추정 $0.00105335; 청구서 대조는 안 함 |
| 미확인 사용량·재시도 | 각각 0 |
| 출처 보존 | 기존 자료 68개 및 원본 Lesson 변경 없음 |
| 저장 연결 | Lesson/요청/전체 생성 요청/응답/JSON 코드/.py hash 일치, 중복 저장 없음 |
| 생성 코드 실행·새 물리 실행 | 모두 0 |

검사 재현:

```powershell
.\runtime\luna-venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p '*grasp_skill_candidate*.py' -q
.\runtime\luna-venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p 'test_*grasp_lesson*.py' -q
.\runtime\luna-venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p test_retrieve_grasp_experience.py -q
.\runtime\luna-venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -p test_export_grasp_experience.py -q
```

실제 실행 로그: `runs/step22i_skill_candidates/20261110T084416_363647Z_b5e28779a297`. 이 폴더의 `candidate.py`는 검토용 사본이며 실행 명령으로 사용하지 않는다. 최종 후보는 `runtime/skill_candidates/simulation/post_grasp_v1/96fb203da360bcc6ca2b876d25a5206986fcf1fc44281003ad6a039026003bee.json`, 추가 출처 검사는 `runtime/validation/step22i_candidate_check.json`이다.

API 형식과 단가는 [OpenAI 공식 함수 호출 문서](https://developers.openai.com/api/docs/guides/function-calling)와 [Luna 모델 문서](https://developers.openai.com/api/docs/models/gpt-6-luna)를 확인했다. 엄격한 함수 응답 형식을 사용하더라도 코드의 동작 의미와 조립 성공은 별도 검증 대상이다.

## 7. 남은 다음 한 단위

이번 요청에 맞춰 22E 계획 중 후보 생성·저장 부분을 먼저 분리 구현했다. 다음은 **제한된 계산 실행기와 출력 adapter**다. 현재 관측의 형식과 범위를 확인하고, 제한된 환경에서 이 함수를 계산해 파지 수치 또는 보류가 나오는지 확인해야 한다. 그 뒤 별도의 MuJoCo 사전검사·실제 조립 실행·독립 측정, 조건부 등록과 동결을 진행한다. 이번 턴에서는 다음 단계를 시작하지 않았다.
