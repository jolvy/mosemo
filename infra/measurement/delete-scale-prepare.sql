-- 7B 측정 생성 전 준비: 인덱스 후보 6개(D-59) + 4개 표 autovacuum 기준 1%(D-57). head 마이그레이션 뒤, 생성 전에 실행한다.
-- delete-scale-steps.sh cleanup이 되돌린다.
\set ON_ERROR_STOP 1
CREATE INDEX IF NOT EXISTS x7b_seg_first ON activity_timeline_segments (first_event_id);
CREATE INDEX IF NOT EXISTS x7b_seg_last ON activity_timeline_segments (last_event_id);
CREATE INDEX IF NOT EXISTS x7b_conf_first ON activity_label_confirmations (first_event_id);
CREATE INDEX IF NOT EXISTS x7b_prop_first ON activity_label_proposals (first_event_id);
CREATE INDEX IF NOT EXISTS x7b_conf_label ON activity_label_confirmations (label_id);
CREATE INDEX IF NOT EXISTS x7b_prop_label ON activity_label_proposals (suggested_label_id);
ALTER TABLE activity_records SET (autovacuum_vacuum_scale_factor = 0.01);
ALTER TABLE activity_timeline_segments SET (autovacuum_vacuum_scale_factor = 0.01);
ALTER TABLE activity_label_confirmations SET (autovacuum_vacuum_scale_factor = 0.01);
ALTER TABLE activity_label_proposals SET (autovacuum_vacuum_scale_factor = 0.01);
