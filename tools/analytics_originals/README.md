# 활동 분석 설계 검증 원본 (임시 보관)

활동 분석 4~6단계 설계 검증에 쓴 속성 테스트 생성기의 원본이다. 저장소 밖에서 만들었으므로
Ruff·ty 검사를 받지 않도록 확장자를 `.py.txt`로 두었다. 실행하려면 임시 폴더에 `.py`로 복사한다
(표준 라이브러리만 쓴다). 7단계 하위 Issue가 이 원본을 저장소 코드로 옮기고, 옮긴 원본은 그 Issue에서 지운다. 상태 모델 원본은
`tools/analytics_protocol_models/`로 옮겼다(#280).

| 폴더 | 옮겨 갈 곳 | 담당 |
| --- | --- | --- |
| `property_tests/` | 분석 계산 모듈 테스트(Hypothesis 전략, 변형 관계 테스트) | 7단계 분석 계산 모듈 Issue |

## property_tests

| 원본 | 내용 |
| --- | --- |
| `gen.py.txt` | 경계 시각(자정, 세션 시작·끝, 일시중지 시작·끝)에 구간 끝을 자주 맞추는 무작위 입력 생성기. 경계·0초 구간이 많이 나오는 분포가 핵심이다 |
| `metamorphic.py.txt` | 변형 관계(입력을 바꿨을 때 결과가 지켜야 할 관계) 검사. `gen`, `ref_adapter`를 쓴다 |
| `ref.py.txt`, `ref_adapter.py.txt`, `psl.py.txt`, `psl_fixture.dat` | `metamorphic`이 쓰던 이전 참조 계산과 PSL 시험 목록. 현재 기준 참조 구현은 `tests/support/analytics_reference`다 |
