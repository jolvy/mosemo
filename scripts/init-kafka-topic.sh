#!/bin/sh
set -eu

# --if-not-exists deliberately leaves existing partition counts untouched.
kafka-topics --bootstrap-server "$1" --create --if-not-exists \
    --topic "${BATCH_TOPIC:-activities.registration-requested}" \
    --partitions "${BATCH_TOPIC_PARTITIONS:-2}" --replication-factor 1
kafka-topics --bootstrap-server "$1" --describe \
    --topic "${BATCH_TOPIC:-activities.registration-requested}"
