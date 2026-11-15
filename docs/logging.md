The application reads `src/mosemo/platform/logging.json` and configures Python logging
with `dictConfig()` during startup. Logs are emitted as one JSON object per line
to standard output so a container runtime can forward them to CloudWatch Logs
without an application file handler or AWS SDK.

A filter on the stdout handler redacts sensitive values from the rendered event
message and allowlisted string fields before formatting. The JSON formatter is
responsible only for selecting the approved fields and serializing them.

Every event includes `timestamp`, `service`, `environment`, `logger`, `level`,
and `event`. The optional fields are `error_code`, `duration_ms`, `request_id`,
`operation`, `outcome`, `attempt`, `retryable`, `status_code`, `method`, `route`,
`request_url`, `work_seconds_gap`, `dropped_zero_pauses`, `source`,
`source_read_ms`, `snapshot_build_ms`, `compute_ms`, and `segment_count`. The first
completion of a focus session logs `work_seconds_gap` (whole seconds of the
session minus its pauses, minus the reported work; negative when the work is
larger) and `dropped_zero_pauses` (zero-length pauses dropped from the request).
Each analytics read (daily stats or focus session analysis) logs one line with
`source` (`stored`, `computed` or `future`), `source_read_ms` (source reads),
`snapshot_build_ms` (building the source snapshot), `compute_ms` (the
calculation) and `segment_count` (segments the calculation read); a stored or
future result logs 0 for the three times and the count.
Each daily stats batch run (`analytics-daily-stats`) adds its counters to the job
result line: `saved`, `superseded`, `stale`, `failed` with `failed_compute`,
`failed_lock_timeout`, `failed_statement_timeout` and `failed_serialization`,
`quarantined_new`, `definition_mismatch`, `aborted` (`connection` or
`definition`), `max_source_read_ms` and `max_compute_ms`. The definition marks
(`analytics-definition-mark`) add `accounts` and `markers`. Do not put user activity URLs, window or activity
titles, account or device identifiers, tokens, cookies, authorization data, or
request and response bodies in log messages or fields. Uvicorn access logging is
disabled because its raw request target can contain authentication values in URL
query parameters.

References:

- [Python logging configuration](https://docs.python.org/3.14/library/logging.config.html#logging.config.dictConfig)
- [Sending Amazon ECS logs to CloudWatch](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using_awslogs.html)
- [Analyzing log data with CloudWatch Logs Insights](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/AnalyzingLogData.html)
- [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
