-- 7B 계정 삭제 운영 규모 측정의 데이터 생성기. delete-scale-steps.sh generate가 부른다.
-- 전제: head 마이그레이션 + delete-scale-prepare.sql(7B 인덱스 후보 6개, 4개 표 autovacuum 기준 1%).
-- 모양(시드 확인값): 사용자 1명 하루 레코드 5,500(현지 08~23시, 12시대 1시간 공백, 세션 연결 약 38%),
--   활동 구간 410 + 공백 구간 1, 제안 약 433, 확정 약 236, 영수증 2,934, 세션 3. 라벨 6은 계정당 고정.
-- 넣는 순서: 날짜 → 활동 시각 1시간 묶음 → 묶음 안에서 모든 사용자를 시각순으로 섞어 INSERT ... ORDER BY 시각.
--   운영처럼 한 사용자의 행이 디스크 여러 페이지에 흩어진다(시드는 계정 단위로 붙어 있다).
-- 사용: CALL x7b_gen_setup(사용자 수, '첫날'); CALL x7b_gen_day(날짜 번호[, 시작 묶음]).
--   날짜 번호는 0부터. 묶음 -1 = 그날 세션, 0~13 = 활동 시각 묶음. 묶음마다 COMMIT하고 x7b_glog에 남긴다.

CREATE TABLE IF NOT EXISTS x7b_gconf (k text PRIMARY KEY, v text NOT NULL);
CREATE TABLE IF NOT EXISTS x7b_gu (u int PRIMARY KEY, account_id uuid NOT NULL, device_id uuid NOT NULL, phase float8 NOT NULL, labels uuid[] NOT NULL);
CREATE TABLE IF NOT EXISTS x7b_ctx (i int PRIMARY KEY, ctx jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS x7b_glog (dn int, chunk int, tbl text, n bigint, ms float8, at timestamptz DEFAULT clock_timestamp());

-- 시각(ms) + 해시로 UUIDv7 조립(버전 7, 변형 8)
CREATE OR REPLACE FUNCTION x7b_v7(ts timestamptz, tag text) RETURNS uuid
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT overlay(overlay(lpad(to_hex(floor(extract(epoch FROM ts) * 1000)::bigint), 12, '0') || substr(md5(tag), 1, 20) placing '7' from 13 for 1) placing '8' from 17 for 1)::uuid
$$;
-- 활동 초 a(0..50400) → 실제 시각: 4시간 뒤(현지 12시)에 1시간 공백
CREATE OR REPLACE FUNCTION x7b_wall(dstart timestamptz, a float8) RETURNS timestamptz
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT dstart + make_interval(secs => round((a + CASE WHEN a >= 14400 THEN 3600 ELSE 0 END)::numeric, 3)::float8)
$$;
-- 사용자 u의 k번째 레코드 활동 초(간격 50400/5500초)
CREATE OR REPLACE FUNCTION x7b_a(phase float8, k int) RETURNS float8
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT phase + k * (50400.0 / 5500) $$;
CREATE OR REPLACE FUNCTION x7b_ev(dstart timestamptz, phase float8, u int, dn int, k int) RETURNS uuid
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT x7b_v7(x7b_wall(dstart, x7b_a(phase, k)), 'e' || u || '.' || dn || '.' || k) $$;
-- 구간 s(0..409)의 첫 레코드 번호, 레코드 k가 속한 구간
CREATE OR REPLACE FUNCTION x7b_s0(s int) RETURNS int LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT s * 5500 / 410 $$;
CREATE OR REPLACE FUNCTION x7b_sk(k int) RETURNS int LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT ((k + 1) * 410 + 5499) / 5500 - 1 $$;
-- 세션 창(활동 초): [1800,8160) [18000,24360) [32400,38760) → 연결 비율 19080/50400 = 37.9%
CREATE OR REPLACE FUNCTION x7b_win(a float8) RETURNS int LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN a >= 1800 AND a < 8160 THEN 0 WHEN a >= 18000 AND a < 24360 THEN 1 WHEN a >= 32400 AND a < 38760 THEN 2 END
$$;

CREATE OR REPLACE PROCEDURE x7b_gen_setup(p_users int, p_first date)
LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM x7b_gconf;
  INSERT INTO x7b_gconf VALUES ('users', p_users), ('first', p_first);
  -- 앱 문맥 8개(시드 smoke-7a 모양, payload 평균 약 320B가 되게)
  DELETE FROM x7b_ctx;
  INSERT INTO x7b_ctx
  SELECT i, jsonb_build_object(
    'app', jsonb_build_object('name', jsonb_build_object('value', n, 'status', 'captured'), 'bundleId', jsonb_build_object('value', b, 'status', 'captured')),
    'web', CASE WHEN w IS NULL THEN '{"kind": "not_applicable"}'::jsonb
                ELSE jsonb_build_object('kind', 'browser', 'tabTitle', jsonb_build_object('value', n || ' 문서', 'status', 'captured'),
                                        'url', jsonb_build_object('value', w, 'status', 'captured')) END,
    'kind', 'detailed',
    'window', '{"reason": "accessibility_permission", "status": "unavailable"}'::jsonb)
  FROM (VALUES (0, 'Terminal', 'com.apple.Terminal', NULL), (1, 'Notion', 'notion.id', NULL), (2, 'Visual Studio Code', 'com.microsoft.VSCode', NULL),
               (3, 'Slack', 'com.tinyspeck.slackmacgap', NULL), (4, 'Google Chrome', 'com.google.Chrome', 'https://docs.example.com/guide'),
               (5, 'Safari', 'com.apple.Safari', 'https://news.example.com/'), (6, 'Figma', 'com.figma.Desktop', NULL), (7, 'Zoom', 'us.zoom.xos', NULL)) v(i, n, b, w);
  DELETE FROM x7b_gu;
  INSERT INTO x7b_gu
  SELECT u, md5('acct' || u)::uuid, md5('dev' || u)::uuid, (('x' || substr(md5('ph' || u), 1, 8))::bit(32)::bigint % 9163) / 1000.0,
         ARRAY(SELECT md5('lbl' || u || '.' || j)::uuid FROM generate_series(1, 6) j ORDER BY j)
  FROM generate_series(1, p_users) u;
  INSERT INTO accounts (id, provider, provider_subject, timezone, created_at, last_authenticated_at)
  SELECT account_id, 'KAKAO', 'delete-scale-' || lpad(u::text, 6, '0'), 'Asia/Seoul', p_first, p_first FROM x7b_gu ORDER BY u;
  INSERT INTO devices (id, account_id, idempotency_key) SELECT device_id, account_id, md5('idem' || u)::uuid FROM x7b_gu ORDER BY u;
  INSERT INTO labels (id, account_id, display_name, created_at, updated_at)
  SELECT g.labels[j], g.account_id, (ARRAY['코딩', '학습', '소통', '문서', '회의', '휴식'])[j], p_first, p_first
  FROM x7b_gu g, generate_series(1, 6) j ORDER BY g.u, j;
END $$;

CREATE OR REPLACE PROCEDURE x7b_gen_day(p_dn int, p_from int DEFAULT -1)
LANGUAGE plpgsql AS $$
DECLARE
  first date := (SELECT v FROM x7b_gconf WHERE k = 'first')::date;
  -- 현지 08:00 시작
  ds timestamptz := ((first + p_dn)::timestamp + interval '8 hours') AT TIME ZONE 'Asia/Seoul';
  c int; t timestamptz; n bigint; stp float8 := 50400.0 / 5500;
BEGIN
  -- 임시 표 통계가 없어 행 수를 크게 잡으면 묶음마다 JIT 컴파일(약 200ms, 측정)이 붙는다
  PERFORM set_config('jit', 'off', false);
  IF p_from <= -1 THEN
  t := clock_timestamp();
  INSERT INTO focus_sessions (id, account_id, device_id, started_at, ended_at, target_seconds, work_seconds, label_id)
  SELECT x7b_v7(x7b_wall(ds, w0), 's' || g.u || '.' || p_dn || '.' || i), g.account_id, g.device_id,
         x7b_wall(ds, w0), x7b_wall(ds, w0) + interval '6360 seconds', 6400, 6360, g.labels[i + 1]
  FROM x7b_gu g, (VALUES (0, 1800.0), (1, 18000.0), (2, 32400.0)) w(i, w0)
  ORDER BY 4;
  GET DIAGNOSTICS n = ROW_COUNT;
  INSERT INTO x7b_glog (dn, chunk, tbl, n, ms) VALUES (p_dn, -1, 'sessions', n, extract(epoch FROM clock_timestamp() - t) * 1000);
  COMMIT;
  END IF;

  FOR c IN greatest(p_from, 0)..13 LOOP
    -- 이 묶음(활동 초 [c*3600, (c+1)*3600))에 드는 사용자별 레코드 번호 범위
    CREATE TEMP TABLE IF NOT EXISTS x7b_kr (u int, account_id uuid, device_id uuid, phase float8, labels uuid[], kf int, kt int) ON COMMIT DELETE ROWS;
    INSERT INTO x7b_kr
    SELECT g.u, g.account_id, g.device_id, g.phase, g.labels,
           greatest(0, ceil((c * 3600 - g.phase) / stp))::int, least(5499, ceil(((c + 1) * 3600 - g.phase) / stp)::int - 1)
    FROM x7b_gu g;

    t := clock_timestamp();
    INSERT INTO activity_records (event_id, device_id, sequence, record_type, observed_at, timezone_id, utc_offset_minutes, payload, received_at, focus_session_id)
    SELECT x7b_ev(ds, r.phase, r.u, p_dn, k), r.device_id, p_dn::bigint * 10000 + k,
           CASE WHEN k = 0 OR k = kg OR k = kg - 1 THEN 'collection_state_changed' ELSE 'activity_observation' END,
           x7b_wall(ds, a), 'Asia/Seoul', 540,
           CASE WHEN k = 0 OR k = kg THEN '{"state": "active", "reason": "user_active"}'::jsonb
                WHEN k = kg - 1 THEN '{"state": "suspended", "reason": "screen_sleep"}'::jsonb
                ELSE jsonb_build_object('context', x.ctx) END,
           x7b_wall(ds, a) + interval '5 seconds',
           CASE WHEN x7b_win(a) IS NOT NULL THEN x7b_v7(x7b_wall(ds, (ARRAY[1800.0, 18000.0, 32400.0])[x7b_win(a) + 1]), 's' || r.u || '.' || p_dn || '.' || x7b_win(a)) END
    FROM x7b_kr r
    CROSS JOIN LATERAL generate_series(r.kf, r.kt) k
    CROSS JOIN LATERAL (SELECT x7b_a(r.phase, k) AS a, ceil((14400 - r.phase) / stp)::int AS kg) q
    JOIN x7b_ctx x ON x.i = (r.u + x7b_sk(k)) % 8
    ORDER BY 5, 2;
    GET DIAGNOSTICS n = ROW_COUNT;
    INSERT INTO x7b_glog (dn, chunk, tbl, n, ms) VALUES (p_dn, c, 'records', n, extract(epoch FROM clock_timestamp() - t) * 1000);

    -- 구간: 마지막 레코드가 이 묶음에 있는 활동 구간(last_event_id FK가 묶음 COMMIT에서 검사됨) + 공백 구간(첫 레코드 = 일시정지 레코드 kg-1)
    t := clock_timestamp();
    INSERT INTO activity_timeline_segments (id, account_id, segment_type, started_at, ended_at, first_event_id, last_event_id, last_observed_at, context, reason)
    SELECT * FROM (
      SELECT x7b_v7(x7b_wall(ds, x7b_a(r.phase, x7b_s0(s))), 'g' || r.u || '.' || p_dn || '.' || s), r.account_id, 'activity',
             x7b_wall(ds, x7b_a(r.phase, x7b_s0(s))),
             x7b_wall(ds, CASE WHEN s = 409 THEN x7b_a(r.phase, 5499) + stp ELSE x7b_a(r.phase, x7b_s0(s + 1)) END),
             x7b_ev(ds, r.phase, r.u, p_dn, x7b_s0(s)),
             x7b_ev(ds, r.phase, r.u, p_dn, CASE WHEN s = 409 THEN 5499 ELSE x7b_s0(s + 1) - 1 END),
             x7b_wall(ds, x7b_a(r.phase, CASE WHEN s = 409 THEN 5499 ELSE x7b_s0(s + 1) - 1 END)),
             x.ctx, NULL::text
      FROM x7b_kr r
      CROSS JOIN LATERAL generate_series(((r.kf + 1) * 410 + 5499) / 5500 - 1, ((r.kt + 2) * 410 + 5499) / 5500 - 2) s  -- 마지막 레코드가 이 묶음에 있는 구간(닫힐 때 기록)
      JOIN x7b_ctx x ON x.i = (r.u + s) % 8
      UNION ALL
      SELECT x7b_v7(ds + interval '4 hours', 'gap' || r.u || '.' || p_dn), r.account_id, 'capture_gap',
             ds + interval '4 hours', ds + interval '5 hours', x7b_ev(ds, r.phase, r.u, p_dn, kg - 1), NULL, NULL, NULL, 'screen_sleep'
      FROM x7b_kr r CROSS JOIN LATERAL (SELECT ceil((14400 - r.phase) / stp)::int AS kg) q
      WHERE kg - 1 BETWEEN r.kf AND r.kt
    ) z ORDER BY 4, 2;
    GET DIAGNOSTICS n = ROW_COUNT;
    INSERT INTO x7b_glog (dn, chunk, tbl, n, ms) VALUES (p_dn, c, 'segments', n, extract(epoch FROM clock_timestamp() - t) * 1000);

    -- 제안: 활동 구간마다 현재 판(ready 또는 s%50=3이면 processing), s%18=7이면 앞선 superseded 판 하나 더
    t := clock_timestamp();
    INSERT INTO activity_label_proposals (id, account_id, first_event_id, segment_version, status, suggested_label_id, attempt_count,
      lease_token, lease_expires_at, provider, model, prompt_version, retrieved_example_ids, latency_ms, input_tokens, output_tokens, suggested_at, created_at, updated_at)
    SELECT x7b_v7(ca, 'p' || r.u || '.' || p_dn || '.' || s || '.' || v), r.account_id, x7b_ev(ds, r.phase, r.u, p_dn, x7b_s0(s)),
           md5('sv' || r.u || '.' || p_dn || '.' || s || '.' || v) || md5('sv2' || r.u || '.' || p_dn || '.' || s || '.' || v),
           st, CASE WHEN st = 'ready' AND s % 10 <> 9 THEN r.labels[(s + 1) % 6 + 1] END, 1,
           CASE WHEN st = 'processing' THEN md5('lease' || r.u || '.' || p_dn || '.' || s)::uuid END,
           CASE WHEN st = 'processing' THEN ca + interval '2 minutes' END,
           CASE WHEN st = 'ready' THEN 'seed' END, CASE WHEN st = 'ready' THEN 'seed' END, CASE WHEN st = 'ready' THEN 'seed' END,
           CASE WHEN st = 'ready' THEN jsonb_build_array(md5('x1' || s)::uuid, md5('x2' || s)::uuid, md5('x3' || s)::uuid, md5('x4' || s)::uuid, md5('x5' || s)::uuid) END,
           CASE WHEN st = 'ready' THEN 2728 END, CASE WHEN st = 'ready' THEN 1560 END, CASE WHEN st = 'ready' THEN 48 END,
           CASE WHEN st = 'ready' THEN ca + interval '2.728 seconds' END, ca, ca
    FROM x7b_kr r
    CROSS JOIN LATERAL generate_series((r.kf * 410 + 5499) / 5500, ((r.kt + 1) * 410 + 5499) / 5500 - 1) s
    CROSS JOIN LATERAL (VALUES (1, CASE WHEN s % 50 = 3 THEN 'processing' ELSE 'ready' END), (0, CASE WHEN s % 18 = 7 THEN 'superseded' END)) vv(v, st)
    CROSS JOIN LATERAL (SELECT x7b_wall(ds, x7b_a(r.phase, x7b_s0(s)) + CASE WHEN v = 1 THEN 60 ELSE 30 END) AS ca) q
    WHERE st IS NOT NULL
    ORDER BY ca, 2;
    GET DIAGNOSTICS n = ROW_COUNT;
    INSERT INTO x7b_glog (dn, chunk, tbl, n, ms) VALUES (p_dn, c, 'proposals', n, extract(epoch FROM clock_timestamp() - t) * 1000);

    -- 확정: 활동 구간의 4/7(57%), 시작 10분 뒤
    t := clock_timestamp();
    INSERT INTO activity_label_confirmations (id, account_id, first_event_id, segment_version, label_id, confirmed_at, updated_at)
    SELECT x7b_v7(ca, 'c' || r.u || '.' || p_dn || '.' || s), r.account_id, x7b_ev(ds, r.phase, r.u, p_dn, x7b_s0(s)),
           md5('sv' || r.u || '.' || p_dn || '.' || s || '.1') || md5('sv2' || r.u || '.' || p_dn || '.' || s || '.1'),
           r.labels[s % 6 + 1], ca, ca
    FROM x7b_kr r
    CROSS JOIN LATERAL generate_series((r.kf * 410 + 5499) / 5500, ((r.kt + 1) * 410 + 5499) / 5500 - 1) s
    CROSS JOIN LATERAL (SELECT x7b_wall(ds, x7b_a(r.phase, x7b_s0(s)) + 600) AS ca) q
    WHERE s % 7 < 4
    ORDER BY ca, 2;
    GET DIAGNOSTICS n = ROW_COUNT;
    INSERT INTO x7b_glog (dn, chunk, tbl, n, ms) VALUES (p_dn, c, 'confirmations', n, extract(epoch FROM clock_timestamp() - t) * 1000);

    -- 영수증: 하루 2,934건, j번 영수증 = 레코드 [j*5500/2934, (j+1)*5500/2934) (1~2개)
    t := clock_timestamp();
    INSERT INTO activity_batch_receipts (account_id, device_id, batch_id, fingerprint, results, processed_at)
    SELECT r.account_id, r.device_id, x7b_v7(pa, 'b' || r.u || '.' || p_dn || '.' || j),
           md5('fp' || r.u || '.' || p_dn || '.' || j) || md5('fp2' || r.u || '.' || p_dn || '.' || j),
           (SELECT jsonb_agg(jsonb_build_object('status', 'stored', 'eventId', x7b_ev(ds, r.phase, r.u, p_dn, k)) ORDER BY k)
              FROM generate_series(j * 5500 / 2934, (j + 1) * 5500 / 2934 - 1) k),
           pa
    FROM x7b_kr r
    CROSS JOIN LATERAL generate_series((r.kf * 2934 + 5499) / 5500, ((r.kt + 1) * 2934 + 5499) / 5500 - 1) j
    CROSS JOIN LATERAL (SELECT x7b_wall(ds, x7b_a(r.phase, (j + 1) * 5500 / 2934 - 1)) + interval '5 seconds' AS pa) q
    ORDER BY pa, 1;
    GET DIAGNOSTICS n = ROW_COUNT;
    INSERT INTO x7b_glog (dn, chunk, tbl, n, ms) VALUES (p_dn, c, 'receipts', n, extract(epoch FROM clock_timestamp() - t) * 1000);
    COMMIT;
  END LOOP;
END $$;
