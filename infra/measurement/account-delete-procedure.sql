-- 7B 계정 삭제 후보(실험용, delete-scale-steps.sh delete가 부른다)
--  'one': 명시 순서, 한 트랜잭션
--  'day': 명시 순서, 레코드를 현지 날짜별로 나눠 커밋(D-58 채택안)
-- 순서: 구간 → 확정·제안 → 레코드 → 일시중지·세션 → 영수증 → 일간 행·표시 → 라벨 → 장치 → 인증 코드 → 계정
-- 단계별 경과(ms)를 NOTICE로 낸다.
CREATE OR REPLACE PROCEDURE x7b_account_delete(p_account uuid, p_mode text)
LANGUAGE plpgsql AS $$
DECLARE
  t timestamptz := clock_timestamp(); t_all timestamptz := clock_timestamp(); n bigint; tz text; d date; dmin date; dmax date;
  txs int := 0; max_ms float8 := 0; t_tx timestamptz := clock_timestamp(); ms float8;
BEGIN
  SELECT timezone INTO tz FROM accounts WHERE id = p_account;
  DELETE FROM activity_timeline_segments WHERE account_id = p_account; GET DIAGNOSTICS n = ROW_COUNT;
  RAISE NOTICE 'segments % rows % ms', n, round(extract(epoch FROM clock_timestamp() - t)::numeric * 1000); t := clock_timestamp();
  DELETE FROM activity_label_confirmations WHERE account_id = p_account; GET DIAGNOSTICS n = ROW_COUNT;
  RAISE NOTICE 'confirmations % rows % ms', n, round(extract(epoch FROM clock_timestamp() - t)::numeric * 1000); t := clock_timestamp();
  DELETE FROM activity_label_proposals WHERE account_id = p_account; GET DIAGNOSTICS n = ROW_COUNT;
  RAISE NOTICE 'proposals % rows % ms', n, round(extract(epoch FROM clock_timestamp() - t)::numeric * 1000); t := clock_timestamp();
  IF p_mode = 'day' THEN
    COMMIT; txs := txs + 1; ms := extract(epoch FROM clock_timestamp() - t_tx) * 1000; max_ms := greatest(max_ms, ms);
    SELECT min((observed_at AT TIME ZONE tz)::date), max((observed_at AT TIME ZONE tz)::date) INTO dmin, dmax
      FROM activity_records WHERE device_id IN (SELECT id FROM devices WHERE account_id = p_account);
    d := dmin;
    WHILE d <= dmax LOOP
      t_tx := clock_timestamp();
      DELETE FROM activity_records WHERE device_id IN (SELECT id FROM devices WHERE account_id = p_account)
        AND observed_at >= (d::timestamp AT TIME ZONE tz) AND observed_at < ((d + 1)::timestamp AT TIME ZONE tz);
      COMMIT; txs := txs + 1; ms := extract(epoch FROM clock_timestamp() - t_tx) * 1000; max_ms := greatest(max_ms, ms);
      d := d + 1;
    END LOOP;
    RAISE NOTICE 'records by day % days, % ms, max tx % ms', dmax - dmin + 1, round(extract(epoch FROM clock_timestamp() - t)::numeric * 1000), round(max_ms::numeric); t := clock_timestamp();
    t_tx := clock_timestamp();
  ELSE
    DELETE FROM activity_records WHERE device_id IN (SELECT id FROM devices WHERE account_id = p_account); GET DIAGNOSTICS n = ROW_COUNT;
    RAISE NOTICE 'records % rows % ms', n, round(extract(epoch FROM clock_timestamp() - t)::numeric * 1000); t := clock_timestamp();
  END IF;
  DELETE FROM focus_session_pauses WHERE focus_session_id IN (SELECT id FROM focus_sessions WHERE account_id = p_account);
  DELETE FROM focus_sessions WHERE account_id = p_account; GET DIAGNOSTICS n = ROW_COUNT;
  RAISE NOTICE 'sessions % rows % ms', n, round(extract(epoch FROM clock_timestamp() - t)::numeric * 1000); t := clock_timestamp();
  DELETE FROM activity_batch_receipts WHERE account_id = p_account; GET DIAGNOSTICS n = ROW_COUNT;
  RAISE NOTICE 'receipts % rows % ms', n, round(extract(epoch FROM clock_timestamp() - t)::numeric * 1000); t := clock_timestamp();
  DELETE FROM analytics_daily_stats WHERE account_id = p_account;
  DELETE FROM analytics_daily_markers WHERE account_id = p_account;
  RAISE NOTICE 'analytics % ms', round(extract(epoch FROM clock_timestamp() - t)::numeric * 1000); t := clock_timestamp();
  DELETE FROM labels WHERE account_id = p_account; GET DIAGNOSTICS n = ROW_COUNT;
  RAISE NOTICE 'labels % rows % ms', n, round(extract(epoch FROM clock_timestamp() - t)::numeric * 1000); t := clock_timestamp();
  DELETE FROM devices WHERE account_id = p_account; GET DIAGNOSTICS n = ROW_COUNT;
  RAISE NOTICE 'devices % rows % ms', n, round(extract(epoch FROM clock_timestamp() - t)::numeric * 1000); t := clock_timestamp();
  DELETE FROM native_auth_codes WHERE account_id = p_account;
  DELETE FROM accounts WHERE id = p_account;
  RAISE NOTICE 'account % ms', round(extract(epoch FROM clock_timestamp() - t)::numeric * 1000);
  IF p_mode = 'day' THEN
    COMMIT; txs := txs + 1; ms := extract(epoch FROM clock_timestamp() - t_tx) * 1000; max_ms := greatest(max_ms, ms);
  END IF;
  RAISE NOTICE 'total % ms (before final commit), txs %, max tx % ms', round(extract(epoch FROM clock_timestamp() - t_all)::numeric * 1000), txs, round(max_ms::numeric);
END $$;
