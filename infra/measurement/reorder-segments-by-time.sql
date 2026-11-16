-- 5단계 T3: 구간·확정 테이블을 시간순으로 다시 써서 운영의 물리 배치를 흉내 낸다(STEP5-PLAN 1.4절, R3).
-- 시드는 사용자 하나씩 적재하므로 한 계정·하루의 구간이 heap 몇 페이지에 몰린다. 운영에서는 1천 명이
-- 동시에 쓰므로 시각 순서로 섞인다. 일회용 측정 DB에만 실행한다.
--
-- 방법: 같은 트랜잭션에서 임시 사본(시각 순서) → TRUNCATE → 사본 INSERT. 인덱스는 INSERT 중에 유지된다.
-- 구간을 참조하는 FK는 없다. 구간·확정이 참조하는 레코드·라벨 FK 검사는 그대로 돈다(PK 조회라 비용이 작다).
-- RDS 마스터 사용자로 실행할 수 있게 session_replication_role 같은 슈퍼유저 설정은 쓰지 않는다.
-- 디스크: 구간 테이블 크기만큼의 임시 사본이 잠깐 더 든다(1천×2일 약 0.47GB).
\set ON_ERROR_STOP on
\timing on

\echo ==== 재배치 전: 계정·하루 구간이 놓인 heap 페이지 수(표본 계정 5개 × 마지막 전날)
WITH d AS (SELECT (max(started_at) AT TIME ZONE 'Asia/Seoul')::date - 1 AS day FROM activity_timeline_segments),
     a AS (SELECT DISTINCT account_id FROM activity_timeline_segments ORDER BY account_id LIMIT 5)
SELECT a.account_id, count(*) AS rows,
       count(DISTINCT (s.ctid::text::point)[0]) AS heap_pages
FROM a JOIN activity_timeline_segments s ON s.account_id = a.account_id, d
WHERE (s.started_at AT TIME ZONE 'Asia/Seoul')::date = d.day
GROUP BY a.account_id ORDER BY a.account_id;

BEGIN;

CREATE TEMP TABLE seg_sorted ON COMMIT DROP AS
  SELECT * FROM activity_timeline_segments ORDER BY started_at, id;
TRUNCATE activity_timeline_segments;
INSERT INTO activity_timeline_segments SELECT * FROM seg_sorted ORDER BY started_at, id;

CREATE TEMP TABLE conf_sorted ON COMMIT DROP AS
  SELECT * FROM activity_label_confirmations ORDER BY confirmed_at, id;
TRUNCATE activity_label_confirmations;
INSERT INTO activity_label_confirmations SELECT * FROM conf_sorted ORDER BY confirmed_at, id;
COMMIT;

-- 인덱스는 INSERT 순서로 채워져 이미 촘촘하지만, 운영 인덱스의 분할 흔적은 흉내 내지 않는다(보고에 적는다).
VACUUM (ANALYZE) activity_timeline_segments;
VACUUM (ANALYZE) activity_label_confirmations;

\echo ==== 재배치 뒤: 같은 표본
WITH d AS (SELECT (max(started_at) AT TIME ZONE 'Asia/Seoul')::date - 1 AS day FROM activity_timeline_segments),
     a AS (SELECT DISTINCT account_id FROM activity_timeline_segments ORDER BY account_id LIMIT 5)
SELECT a.account_id, count(*) AS rows,
       count(DISTINCT (s.ctid::text::point)[0]) AS heap_pages
FROM a JOIN activity_timeline_segments s ON s.account_id = a.account_id, d
WHERE (s.started_at AT TIME ZONE 'Asia/Seoul')::date = d.day
GROUP BY a.account_id ORDER BY a.account_id;

\echo ==== 겹침 검사(Q1b 전제: 한 계정의 구간은 서로 겹치지 않는다). 0이어야 한다
SELECT count(*) AS overlapping_pairs FROM (
  SELECT account_id, started_at,
         lag(COALESCE(ended_at, last_observed_at, 'infinity')) OVER (PARTITION BY account_id ORDER BY started_at, id) AS prev_end
  FROM activity_timeline_segments) x
WHERE prev_end > started_at;
