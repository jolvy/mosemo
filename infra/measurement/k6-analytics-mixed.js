// 측정 B: 고정 부하(read·write, 측정 A와 같음)에 새 분석 API 시나리오를 더한다(k6 v2.3.0).
// 회차 종류(ROUND = R1·R2·R3)의 시나리오와 도착률은 PLAN(measurement-b.json)의 rounds에서 읽는다.
// read·write는 k6-common.js 함수를 그대로 쓴다(TOKENS·PAYLOADS·DEVICES·CONTEXTS). 새 시나리오는 TOKENS_B(tokens-b.json)를 쓴다.
// 비율이 소수라 timeUnit 100s에 rate = round(목표 × 100)으로 준다(analytics_measurement.py k6_rate와 같음).
import http from 'k6/http';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { BASE, COMMON, phase, readOne, writeOne, vus } from './k6-common.js';

const PLAN = JSON.parse(open(__ENV.PLAN));
const RATES = PLAN.rounds[__ENV.ROUND];
const DUR = Number(__ENV.DURATION_SEC || PLAN.duration_s);

// tokens-b.json {today, seed_dates, today_share, tokens:[...], sessions:[[계정 순번, 세션 ID]], heavy, probes:[...]}
const B = new SharedArray('b', () => [JSON.parse(open(__ENV.TOKENS_B))])[0];

function get(path, token, name) {
  http.get(`${BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    tags: { phase: phase(), name: name },
    timeout: '30s',
  });
}

function scenario(fn, rps) {
  return { executor: 'constant-arrival-rate', exec: fn, rate: Math.max(1, Math.round(rps * 100)), timeUnit: '100s',
           duration: `${DUR}s`, ...vus(rps) };
}

export const options = {
  ...COMMON,
  scenarios: Object.fromEntries(Object.entries(RATES).map(([name, rps]) => [name, scenario(name, rps)])),
  thresholds: {
    // 측정 중 멈춤(명백한 실패만): 측정 A와 같다. 판정은 측정 뒤 analytics_measurement.py report-b가 한다
    'http_req_failed{phase:main}': [{ threshold: 'rate<0.05', abortOnFail: true, delayAbortEval: '60s' }],
    'http_req_duration{phase:main}': [{ threshold: 'p(99)<5000', abortOnFail: true, delayAbortEval: '60s' }],
  },
};

export function read() {
  readOne();
}

export function write() {
  writeOne();
}

export function stats() {
  const token = B.tokens[Math.floor(Math.random() * B.tokens.length)];
  const day = Math.random() < B.today_share ? B.today : B.seed_dates[Math.floor(Math.random() * B.seed_dates.length)];
  get(`/api/v1/analytics/daily-stats?date=${day}`, token, 'daily_stats');
}

export function sessions() {
  const s = B.sessions[Math.floor(Math.random() * B.sessions.length)];
  get(`/api/v1/analytics/focus-sessions/${s[1]}`, B.tokens[s[0]], 'focus_session');
}

export function heavy() {
  get(`/api/v1/analytics/daily-stats?date=${B.today}`, B.heavy, 'daily_stats_heavy');
}

export function probe() {
  const token = B.probes[exec.scenario.iterationInTest % B.probes.length];
  get(`/api/v1/analytics/daily-stats?date=${B.today}`, token, 'daily_stats_probe');
}
