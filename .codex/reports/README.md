# 에이전트 일일 보고서

지표 로그와 세션 기록으로 mosemo 에이전트 일일 보고서를 만든다.

## 파일 위치

| 파일 | 내용 |
| --- | --- |
| `.codex/reports/aggregate.py` | 지표 로그와 세션 기록의 결정적 집계 |
| `.codex/reports/run_daily.py` | 보고서 실행기: 구간·건너뛰기 판정, `claude -p` 분석, 보고서 조립, 알림 |
| `.codex/reports/install_launchd.py` | launchd plist 생성(파일만 쓴다) |
| `.codex/skills/agent-daily-report/SKILL.md` | 분석 모델에 넘기는 보고서 판단 지침 |

실행기는 보고서 폴더(기본 `~/mosemo-reports/`) 아래에만 쓴다.

| 보고서 폴더 아래 | 내용 |
| --- | --- |
| `YYYY-MM-DD.md` | 보고서(Asia/Seoul 실행 날짜). 같은 이름이 있으면 `-HHMMSS`를 붙인다 |
| `.state.json` | 마지막 성공 보고서의 경계 `{"boundary": "UTC ISO 8601"}` |
| `.runs.jsonl` | 실행마다 한 줄(`status`: `report`·`skipped`·`failed`) |
| `.work/` | 분석 작업 폴더(집계 JSON, 세션 기록 복사본). 보고서를 만드는 실행마다 다시 만든다 |
| `.logs/` | launchd 표준 출력·오류 |

지표 로그는 `MOSEMO_METRICS_LOG`, 없으면 저장소 공통 git 디렉터리의 `mosemo-agent-metrics.jsonl`이다.

## 수동 실행

저장소 본체에서 실행한다.

```sh
uv run --locked python .codex/reports/run_daily.py
```

다른 보고서 폴더는 `--reports-dir PATH`로 지정한다. 새 세션이 5개 미만이면 `.runs.jsonl`에 `skipped`만 남기고 끝난다.

## 설치와 롤백

```sh
uv run --locked python .codex/reports/install_launchd.py
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.jolvy.mosemo-agent-report.plist
```

`install_launchd.py`는 plist 파일만 쓰고 등록은 하지 않는다. `--plist`·`--reports-dir`로 경로를 바꿀 수 있다.

롤백:

```sh
launchctl bootout gui/$(id -u)/dev.jolvy.mosemo-agent-report
rm ~/Library/LaunchAgents/dev.jolvy.mosemo-agent-report.plist
```
