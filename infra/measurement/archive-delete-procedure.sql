-- 야간 이관의 DB 삭제 흉내(실험용, delete-scale-steps.sh delete가 부른다). S3 복사·확인은 빼고 DB 삭제만.
-- 계정의 현지 날짜 [t0, t1) 범위:
--   ③ 그 날짜에 시작한 구간의 확정·제안 → 구간 삭제
--   ④ 그 날짜 레코드 중 남은 구간이 가리키지 않는 것 삭제(확정·제안은 CASCADE)
-- mode 'account': 계정 하나 = 트랜잭션 하나
-- mode 'rows'   : 계정 안에서 레코드를 p_rows 행씩 나눠 커밋(③은 한 트랜잭션)
CREATE OR REPLACE PROCEDURE x7b_archive_delete(p_day date, p_mode text, p_rows int DEFAULT 2000)
LANGUAGE plpgsql AS $$
DECLARE
  a record; t0 timestamptz; t1 timestamptz; n int; t_start timestamptz; tx_ms float8; max_ms float8 := 0; txs int := 0;
BEGIN
  FOR a IN SELECT id, timezone FROM accounts ORDER BY id LOOP
    t0 := p_day::timestamp AT TIME ZONE a.timezone;
    t1 := (p_day + 1)::timestamp AT TIME ZONE a.timezone;
    t_start := clock_timestamp();
    DELETE FROM activity_label_confirmations c USING activity_timeline_segments s
      WHERE s.account_id = a.id AND s.started_at >= t0 AND s.started_at < t1
        AND c.account_id = a.id AND c.first_event_id = s.first_event_id;
    DELETE FROM activity_label_proposals p USING activity_timeline_segments s
      WHERE s.account_id = a.id AND s.started_at >= t0 AND s.started_at < t1
        AND p.account_id = a.id AND p.first_event_id = s.first_event_id;
    DELETE FROM activity_timeline_segments s
      WHERE s.account_id = a.id AND s.started_at >= t0 AND s.started_at < t1;
    IF p_mode = 'account' THEN
      DELETE FROM activity_records r
        WHERE r.device_id IN (SELECT id FROM devices WHERE account_id = a.id)
          AND r.observed_at >= t0 AND r.observed_at < t1
          AND NOT EXISTS (SELECT 1 FROM activity_timeline_segments s WHERE s.first_event_id = r.event_id)
          AND NOT EXISTS (SELECT 1 FROM activity_timeline_segments s WHERE s.last_event_id = r.event_id);
      COMMIT; txs := txs + 1;
      tx_ms := extract(epoch FROM clock_timestamp() - t_start) * 1000; max_ms := greatest(max_ms, tx_ms);
    ELSE
      COMMIT; txs := txs + 1;
      tx_ms := extract(epoch FROM clock_timestamp() - t_start) * 1000; max_ms := greatest(max_ms, tx_ms);
      LOOP
        t_start := clock_timestamp();
        DELETE FROM activity_records r WHERE r.event_id IN (
          SELECT r2.event_id FROM activity_records r2
          WHERE r2.device_id IN (SELECT id FROM devices WHERE account_id = a.id)
            AND r2.observed_at >= t0 AND r2.observed_at < t1
            AND NOT EXISTS (SELECT 1 FROM activity_timeline_segments s WHERE s.first_event_id = r2.event_id)
            AND NOT EXISTS (SELECT 1 FROM activity_timeline_segments s WHERE s.last_event_id = r2.event_id)
          LIMIT p_rows);
        GET DIAGNOSTICS n = ROW_COUNT;
        COMMIT; txs := txs + 1;
        tx_ms := extract(epoch FROM clock_timestamp() - t_start) * 1000; max_ms := greatest(max_ms, tx_ms);
        EXIT WHEN n < p_rows;
      END LOOP;
    END IF;
  END LOOP;
  RAISE NOTICE 'x7b_archive_delete day=% mode=% txs=% max_tx_ms=%', p_day, p_mode, txs, round(max_ms::numeric, 1);
END $$;
