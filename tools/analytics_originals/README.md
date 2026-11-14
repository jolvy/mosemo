# 활동 분석 설계 검증 원본 (임시 보관)

활동 분석 4~6단계 설계 검증에 쓴 상태 모델과 속성 테스트 생성기의 원본이다. 저장소 밖에서 만들었으므로
Ruff·ty 검사를 받지 않도록 확장자를 `.py.txt`로 두었다. 실행하려면 임시 폴더에 `.py`로 복사한다
(표준 라이브러리만 쓴다). 7단계 하위 Issue가 이 원본을 저장소 코드로 옮기고, 옮긴 원본은 그 Issue에서 지운다.

| 폴더 | 옮겨 갈 곳 | 담당 |
| --- | --- | --- |
| `protocol_models/` | `tools/analytics_protocol_models/`(Ruff·ty 정리, CI용 작은 시나리오) | 7단계 상태 모델 도구화 Issue |
| `property_tests/` | 분석 계산 모듈 테스트(Hypothesis 전략, 변형 관계 테스트) | 7단계 분석 계산 모듈 Issue |

## protocol_models

ADR 0016 「일간 통계 프로토콜」의 전수 탐색 모델이다. 각 모델은 추천 설계 변형과 대조 변형을 함께 탐색한다.
`runs/`는 마지막 전체 실행 출력이며, 변형별 기대 결과(통과·위반 불변식)의 기준이다.

| 원본 | 대상 | 추천 변형(위반 0이어야 함) | 대조 변형(기대한 위반이 나와야 함) | 실행 출력 |
| --- | --- | --- | --- | --- |
| `protocol_model.py.txt` | 표시·배치·저장 guard·정의 확인·조회 스냅샷(4단계 D) | V0, V0s, V0r, V5, V8, V11 | V1~V4, V5s, V6, V7, V9, V10, V12, V12s, V12b, V13~V16 | `runs/protocol_model_run.txt` 끝 요약 |
| `protocol_model_4b.py.txt` | 재시도·lease·격리 열(4B F-1) | F5 | N2(I1·I2·I6), N3(I7), N5(I4). N1·N4는 통과가 기대값(lease와 실패 기록의 version 조건은 정확성 장치가 아님) | 없음(F5 위반 0, 상태 약 296만) |
| `protocol_model_tz.py.txt` | 계정 시간대 변경 1차(F-2) | Z1, Z1x, Z9u | 나머지 Z 변형 | `runs/protocol_model_tz_run.txt` 끝 요약 |
| `protocol_model_tz3.py.txt` | 시간대 변경 표시 대상(F-2 3차) | Ax, A2 | A0·As·Ast(I8), A0n, A0h, A0q | `runs/protocol_model_tz3_run.txt` 끝 요약 |
| `protocol_model_tz3_distinct.py.txt` | tz3 결과의 차이 분류 보조 | — | — | `runs/protocol_model_tz3_distinct_run.txt` |
| `protocol_model_archive.py.txt` | 90일 아카이브·S3 재계산·동결(F-3, 7B 범위) | R3, R3f | 실행 출력 참조 | `runs/protocol_model_archive_run_full.txt` |

주의: ADR 0016 「시간대 변경」은 표시 대상을 "저장 행이 있는 날짜와 표시가 있는 날짜, 그리고 각각의 앞뒤 하루"로
정했다. tz3의 A0(같은 규칙)이 I8을 보고하는 경우는 `runs/protocol_model_tz3_distinct_run.txt`가 분류한
"빠진 날짜에 새 tz 활동 있음"(저장 행·표시가 없는 날짜)이며, 그런 날짜는 조회가 원천에서 계산한다. 규칙의 원본은 ADR이다.

전체 탐색은 모델마다 수 분~10분, 메모리 1GB 안팎이다. CI에는 모델마다 작은 시나리오만 둔다.

## property_tests

| 원본 | 내용 |
| --- | --- |
| `gen.py.txt` | 경계 시각(자정, 세션 시작·끝, 일시중지 시작·끝)에 구간 끝을 자주 맞추는 무작위 입력 생성기. 경계·0초 구간이 많이 나오는 분포가 핵심이다 |
| `metamorphic.py.txt` | 변형 관계(입력을 바꿨을 때 결과가 지켜야 할 관계) 검사. `gen`, `ref_adapter`를 쓴다 |
| `ref.py.txt`, `ref_adapter.py.txt`, `psl.py.txt`, `psl_fixture.dat` | `metamorphic`이 쓰던 이전 참조 계산과 PSL 시험 목록. 현재 기준 참조 구현은 `tests/support/analytics_reference`다 |
