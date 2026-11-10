# 22H — 검색 경험으로 교훈 생성·저장

작성: 2026-11-10. 상태: **구현·계약 검사·실제 Luna 교훈 1개 생성·저장 확인 완료**.

## 1. Memory와 Lesson의 차이

Memory는 “Z=0 파지가 사전검사에서 거절됐고, 같은 회차의 후속 제안은 Z=20으로 바뀌어 삽입 검증을 통과했다”라는 사건 기록이다.

Lesson은 그 기록을 해석한 **조건부 가설**이다. 이번 실제 생성 결과를 쉽게 풀면 “이런 조건의 기둥에서 중앙 파지가 삽입 간섭으로 거절되면, 높이를 올리는 방법을 현재 장면에서 다시 검사할 후보로 검토할 수 있다”다.

교훈은 실행 코드가 아니다. 기록된 높이를 곧바로 다음 로봇에 명령하거나, 생성된 자연어를 물리 성공 판정으로 쓰지 않는다. 저장 상태는 `provisional_hypothesis`이며 실행 권한·자동 스킬 등록·새 물리 검증은 모두 false다.

## 2. 어떤 부분을 Luna가 작성하는가

| 부분 | 작성 주체 | 확인 방법 |
|---|---|---|
| 파지값·접촉·검사 거절·실행·삽입 측정 | 프로그램이 원본에서 복사 | 22F/22G 검증과 원본 export 재확인 |
| 로봇·그리퍼·치수·결합·좌표/단위·관측 조건 | 프로그램이 원본에서 복사 | 관측한 조건 목록 보존, 임의 범위 확대 금지 |
| 교훈 제목·가설·짧은 설명 | Luna | 가설로 저장, 의미·인과관계가 검증됐다고 표시하지 않음 |
| 지지 근거·반례 후보·미확인 근거 ID | Luna | 존재 여부·중복·전체 검토·미확인 누락 검사 |
| 적용·제외 조건에 대한 자연어 설명 | Luna | 별도 해석으로 저장, 프로그램이 보존한 조건을 변경하지 못함 |

모델이 보편적인 범위를 적더라도 기계가 사용하는 적용 조건은 관측된 사례 목록에 묶인다. 이번 자동 검사는 형식·근거 연결·범위 보존을 확인한다. 자연어 문장 하나하나의 진실이나 인과관계까지 증명하는 시스템은 아니다.

## 3. 데이터가 흐르는 순서

1. 현재 부품과 실패 상황으로 22G 검색을 수행한다.
2. 검색을 다시 확인하고 선택된 export·snapshot·record를 원본과 대조한다. 바뀐 검색 결과나 없는 근거는 거절한다.
3. 실패와 후속 수정 전체를 근거 묶음에 담는다. 짧은 `F1`, `F2` 같은 별칭은 원래 시도·기록 ID로 연결된다.
4. Luna에 근거 묶음을 전달하고 교훈 1개 또는 보류를 요청한다.
5. 사용량을 정산한 뒤 응답 형식·근거 ID·미확인 기록 누락 여부를 검사한다.
6. 호스트가 관측 사실·적용 범위·해석을 분리한 Lesson을 만든다.
7. 저장 직전에 원본을 다시 검사하고, 내용 hash 이름의 JSON으로 불변 저장한다.

근거가0건이면 모델을 호출하지 않는다. Luna가 `abstain`을 선택하면 요청·응답·보류 사유를 실행 로그에 남기고 Lesson은 등록하지 않는다. 호출 또는 응답 검증 오류에도 자동 재시도하지 않는다.

## 4. 구현 파일

- `scripts/grasp_lessons.py`: 근거 묶음, 모델 입력/출력 계약, 교훈 구성·검증·저장.
- `scripts/generate_grasp_lesson.py`: 검색부터 preview 또는 예산 제한 모델 호출까지 실행하는 명령.
- `tests/test_grasp_lessons.py`: 근거·조건·저장 검사29개.
- `tests/test_generate_grasp_lesson.py`: 요청·비용·보류·오류·실제/모의 호출 구분 검사6개.

기존 22F 저장기, 22G 검색기, 조립 정책·동작·검증기는 수정하지 않았다. 모델 호출은 기존 `llm_runtime.py`의 클라이언트와 비용 장부를 재사용한다.

Lesson은 `runtime/lessons/simulation/post_grasp_v1/`에, 요청·응답·근거·비용·결과는 `runs/step22h_lessons/`에 저장한다. 둘 다 OneDrive 밖이며 Git에서 제외한다. 기존 Memory/Skill 저장소와 원본 실행 폴더에는 Lesson을 쓰지 못하게 했다.

## 5. 실제 Luna 생성 결과

- 실행: `runs/step22h_lessons/20261110T081605_228818Z_f602db6cf9ee`.
- 모델: 요청·응답 모두 `gpt-6-luna`.
- Lesson: `60e35a84545589bd6752c2f4e20040278b5a861709bf1aad31e65374d1337393`.
- 생성 제목: **프리플라이트 접촉 후 그립 z 조정은 조건부 후보**.
- 근거: 별도 개발 회차3개, 파지 시도6개. 현재 질문에 사용한21N 회차는 제외했다.

| 근거 | 기록된 사실 |
|---|---|
| F1 → F2 | 21K post1: Z=0 거절 → Z=20mm 수정, 수치 삽입 검증 기록 |
| F3 → F4 | 21M post1: Z=0 거절 → Z=4mm 수정, 수치 삽입 검증 기록 |
| F5 → F6 | 21F 트레이 변경 post1: Z=0 거절 → Z=12mm 수정, 수치 삽입 검증 기록 |

Luna는 F2/F4/F6을 지지 근거로, F1/F3/F5를 실제 집기·삽입 결과가 없는 근거로 표시했고6개 모두 검토했다고 반환했다. 초기 거절3개의 상태는 호스트가 별도로 확정된 사전검사 거절로 보존한다. “미확인”이라는 모델 분류가 이미 관측된 거절 사실을 지우지 않는다.

적용 범위에는 UR3e·2F-85·부품 기준 mm/deg·위쪽 접근·관측한 기둥/결합 조건을 남겼다. 다른 치수·형상·TCP 보정·접촉/설치 조건·실물과 파지 중 재파지는 검증 범위에 포함하지 않는다. 현 장면의 새 검사도 필요하다.

원문 가설의 “같은 시도에서 … 세 값” 표현은 주의해서 읽어야 한다. 세 높이는 **서로 다른 회차의 수정 시도**에서 관측됐다. 원문은 덮어쓰지 않았고 위 표와 구조화된 사실이 실제 구분을 보존한다. 따라서 이번 저장 완료를 자연어 의미 검증 완료로 해석하지 않는다.

## 6. 사용량과 검증 결과

- 실제 생성 **1회**, 토큰 계산 포함 HTTP **2회**, 자동 재시도0회.
- 입력3,571토큰, 출력686토큰, 합계4,257토큰.
- 설정 단가에 따른 추정 비용 **USD0.000785975**. 청구서를 대조한 금액은 아니다.
- 이 실행의 비용 상한0.01달러, 생성1회, 입력12,000·출력4,096·합계16,096토큰 상한.
- 실제 요청 전 키 로딩이 한 번 실패했지만 그 단계에서는 모델 요청을 보내지 않았다. 암호문을 읽을 때 줄바꿈을 제거한 후 정상 호출했다. 키·암호문 내용은 출력하거나 저장소에 기록하지 않았다.
- 새 교훈 검사 **35개 통과**(5.580초), 기존 검색 **22개 통과**(1.701초), 기존 변환 **29개 통과**(1.096초).
- 실제 요청/생성 요청/응답 hash, 모델·응답 ID, 근거 묶음 ID와 Lesson 연결을 대조했다.
- 동일 Lesson 재저장 시 새 파일을 만들지 않는 것을 확인했다.
- 원본·기존 지식·관련 코드 **68개 파일 hash 동일**을 확인했다.
- 새 물리 시뮬레이션·로봇 명령·Python 스킬 생성 **0회**.

실제 확인 보고서는 `runtime/validation/step22h_lesson_check.json`이다. 모의 클라이언트 검사는 `offline_fixture`로 표시하며, 모의 요청 수와 실제 API 호출 수를 분리한다. 모의 저장 성공을 실제 Luna 생성으로 집계하지 않는다.

## 7. 실행 방법

키 없이 근거와 요청만 확인:

```powershell
cd C:\dev\llm-robot-assembly
.\runtime\luna-venv\Scripts\python.exe scripts/generate_grasp_lesson.py --run-dir runs/step23_frame/20261105T052814_462220Z_48e7b620 --part post_1 --failure-code PLANNED_CONTACT --failure-phase insert_lower/preflight --preview
```

예상 결과는 `status: preview`, `API_calls: 0`이다. 설정을 생략하면 항상 preview이며, API 키를 읽지 않는다.

프로젝트 키가 환경변수에 설정된 상태에서 실제 생성:

```powershell
.\runtime\luna-venv\Scripts\python.exe scripts/generate_grasp_lesson.py --run-dir runs/step23_frame/20261105T052814_462220Z_48e7b620 --part post_1 --failure-code PLANNED_CONTACT --failure-phase insert_lower/preflight --llm-config runs/step22h-budget.toml
```

성공하면 `status: lesson_saved`와 `lesson_path`가 나온다. 매번 새 모델 호출이므로 같은 교훈이 보장되지 않는다. 저장 기능 자체의 중복 방지는 같은 Lesson 객체를 다시 저장할 때 적용된다.

기존 Windows 프로젝트 키를 현재 프로세스에서만 불러오는 방식:

```powershell
$lessonKeyPath = Join-Path $env:LOCALAPPDATA 'llm-robot-assembly-openai.dpapi'
$lessonSecureKey = ConvertTo-SecureString -String ([IO.File]::ReadAllText($lessonKeyPath).Trim())
try {
    $env:OPENAI_API_KEY = [Net.NetworkCredential]::new('', $lessonSecureKey).Password
    # 위의 실제 생성 명령을 이 위치에서 실행한다.
} finally {
    Remove-Item Env:\OPENAI_API_KEY -ErrorAction SilentlyContinue
    $lessonSecureKey = $null
}
```

이 컴퓨터에 등록된 키 파일을 사용하는 예시다. 키·암호문을 화면에 출력하거나 Git에 넣지 않는다. 새 컴퓨터에는 로컬 run·경험·키·`runs/step22h-budget.toml`이 없으므로 별도 준비가 필요하다. 설정 형식은 `config/llm.example.toml`을 참고하고 명시적 모델·검증한 단가·예산을 지정한다.

검사:

```powershell
.\runtime\luna-venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_*grasp_lesson*.py' -v
```

## 8. 한계와 다음 단위

현재는 기둥 사전검사 실패의 개발 참고용 Lesson이다. 근거 없는 ID·누락·조건 변경을 검사하지만 자연어 가설의 타당성을 완전히 자동 검증하지 않는다. 반례 목록이 비어 있어도 반례가 없다는 증거가 아니며, 검색된 제한된 사례 밖으로 조사하지 않았다.

Lesson을 조립 중 Luna에게 전달하거나 실행 코드를 생성·등록하는 기능은 아직 없다. 이후 제한 Python 스킬을 만들 때도 이 교훈은 후보를 제안하는 참고 근거이며, 별도 MuJoCo 검증·등록·동결이 필요하다. 현재 `training_eligibility`는 평가/학습 구분이 아직 배정되지 않은 상태를 유지한다.

API 구현 형식은 [공식 function calling 문서](https://developers.openai.com/api/docs/guides/function-calling)를 확인했다. 모델·단가는2026-11-10 확인한 [공식 Luna 문서](https://developers.openai.com/api/docs/models/gpt-6-luna)를 근거로 기존 설정과 대조했다.
