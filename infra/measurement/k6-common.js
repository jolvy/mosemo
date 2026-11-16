// API 한계·기준 사양 측정 — k6 v2.3.0 공통 함수.
// 진입 파일: k6-mixed-read-upload.js(조회+업로드 고정 비율).
// 데이터는 Pod 안에서 k6_inputs_and_reports.py가 미리 만든다(JWT는 Secret 키로 거기서 서명. k6에서 서명하지 않음).
// 파일 파싱은 SharedArray 콜백 안에서만 한다(init 코드는 VU마다 돌아 밖에서 파싱하면 VU 수만큼 메모리를 쓴다).
import http from 'k6/http';
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';

export const BASE = __ENV.BASE_URL;
export const WARM = Number(__ENV.WARM || 30);

// ---- 조회: tokens.json {today, accounts:[{token, past:[날짜]}]}
const ACC = __ENV.TOKENS ? new SharedArray('accounts', () => JSON.parse(open(__ENV.TOKENS)).accounts) : null;
const TODAY = __ENV.TOKENS ? new SharedArray('today', () => [JSON.parse(open(__ENV.TOKENS)).today])[0] : null;
const SHARE = Number(__ENV.TODAY_SHARE || 0.9);

// ---- 업로드: payloads-*.json {t0, bid_prefix, eid_prefix, chunk, n, chunks:["di,seq,k,ctx;..."]}, devices.json, contexts.json
// 관찰 시각은 전역 순번 × 2ms로 늘고 sequence는 기기마다 순번 순서라, 같은 기기 요청이 겹치거나 순서가 바뀌어도
// consumer 기기 순서 검사(projection_time_regression)를 통과한다.
const CHUNKS = __ENV.PAYLOADS ? new SharedArray('chunks', () => JSON.parse(open(__ENV.PAYLOADS)).chunks) : null;
const META = __ENV.PAYLOADS
  ? new SharedArray('meta', () => {
      const p = JSON.parse(open(__ENV.PAYLOADS));
      return [{ t0: p.t0, bid: p.bid_prefix, eid: p.eid_prefix, chunk: p.chunk, n: p.n }];
    })[0]
  : null;
const DEVICES = __ENV.DEVICES ? new SharedArray('devices', () => JSON.parse(open(__ENV.DEVICES)).devices) : null;
const DTOKENS = __ENV.DEVICES ? new SharedArray('dtokens', () => JSON.parse(open(__ENV.DEVICES)).tokens) : null;
const CTX = __ENV.CONTEXTS ? new SharedArray('contexts', () => JSON.parse(open(__ENV.CONTEXTS)).contexts) : null;

export function phase() {
  return Date.now() - exec.scenario.startTime < WARM * 1000 ? 'warm' : 'main';
}

export function readOne() {
  const a = ACC[Math.floor(Math.random() * ACC.length)];
  const past = a.past;
  const day = Math.random() < SHARE || past.length === 0 ? TODAY : past[Math.floor(Math.random() * past.length)];
  http.get(`${BASE}/api/v1/activities/timeline?date=${day}`, {
    headers: { Authorization: `Bearer ${a.token}` },
    tags: { phase: phase(), name: 'timeline' },
    timeout: '30s',
  });
}

function hex12(n) {
  return n.toString(16).padStart(12, '0');
}

export function writeOne() {
  const i = exec.scenario.iterationInTest; // 업로드 시나리오 안의 순번 = 묶음 순번
  if (i >= META.n) return; // 준비한 묶음보다 많이 돌면 보내지 않는다(준비량 rate × 시간 × 1.1)
  const r = CHUNKS[Math.floor(i / META.chunk)].split(';')[i % META.chunk].split(',').map(Number);
  const bid = META.bid + hex12(i);
  const records = [];
  for (let j = 0; j < r[2]; j++) {
    records.push({
      eventId: META.eid + hex12(2 * i + j),
      sequence: r[1] + j,
      observedAt: new Date(META.t0 + 2 * i + j).toISOString(),
      timezoneId: 'Asia/Seoul',
      utcOffsetMinutes: 540,
      recordType: 'activity_observation',
      context: CTX[r[3]],
    });
  }
  http.post(`${BASE}/api/v1/activities/batches`, JSON.stringify({ batchId: bid, deviceId: DEVICES[r[0]], records: records }), {
    headers: { Authorization: `Bearer ${DTOKENS[r[0]]}`, 'Content-Type': 'application/json' },
    tags: { phase: phase(), name: 'batches', bid: bid },
    timeout: '30s',
  });
}

export const COMMON = {
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max', 'count'],
  systemTags: ['status', 'method', 'name', 'scenario', 'error_code', 'expected_response'],
};

// VU 상한 600: 측정 Pod tools 768Mi 안(로컬 실측 최악 274MB). 넘치면 dropped_iterations → 발생기 포화로 표시
export function vus(rate) {
  const max = Number(__ENV.MAX_VUS || Math.min(600, Math.max(100, Math.ceil(rate * 4))));
  return { preAllocatedVUs: Number(__ENV.PRE_VUS || Math.min(Math.max(20, Math.ceil(rate)), max)), maxVUs: max };
}
