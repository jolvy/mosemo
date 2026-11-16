// 고정 부하(조회 + 업로드, workload-*.json의 비율)로 총 TOTAL_RPS를 DURATION_SEC 동안 유지한다(k6 v2.3.0).
// 시나리오 두 개(read·write)를 동시에 constant-arrival-rate로 돌린다. 비율은 workload-*.json의 read_share.
// 비율이 소수라 timeUnit 100s에 rate = 목표 × 100(정수)으로 준다. 7단계 뒤 같은 파일·같은 인자로 다시 잰다.
import { COMMON, readOne, writeOne, vus } from './k6-common.js';

const W = JSON.parse(open(__ENV.WORKLOAD)); // init에서 VU마다 읽지만 수백 바이트라 괜찮다
const TOTAL = Number(__ENV.TOTAL_RPS || W.total_rps);
const DUR = Number(__ENV.DURATION_SEC || 900);
const READ_RPS = TOTAL * W.read_share;
const WRITE_RPS = TOTAL - READ_RPS;

function scenario(fn, rps) {
  return { executor: 'constant-arrival-rate', exec: fn, rate: Math.max(1, Math.round(rps * 100)), timeUnit: '100s',
           duration: `${DUR}s`, ...vus(rps) };
}

export const options = {
  ...COMMON,
  scenarios: { read: scenario('read', READ_RPS), write: scenario('write', WRITE_RPS) },
  thresholds: {
    // 측정 중 멈춤(명백한 실패만): 오류율 > 5% 또는 p99 > 5초. 판정은 workload-*.md 기준으로 측정 뒤 한다
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
