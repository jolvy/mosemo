The application reads `src/mosemo/logging.json` and configures Python logging
with `dictConfig()` during startup. Logs are emitted as one JSON object per line
to standard output so a container runtime can forward them to CloudWatch Logs
without an application file handler or AWS SDK.

A filter on the stdout handler redacts sensitive values from the rendered event
message and allowlisted string fields before formatting. The JSON formatter is
responsible only for selecting the approved fields and serializing them.

Every event includes `timestamp`, `service`, `environment`, `logger`, `level`,
and `event`. The optional fields are `error_code`, `duration_ms`, `request_id`,
`operation`, `outcome`, `attempt`, `retryable`, `status_code`, `method`, `route`,
and `request_url`. Do not put user activity URLs, window or activity
titles, account or device identifiers, tokens, cookies, authorization data, or
request and response bodies in log messages or fields. Uvicorn access logging is
disabled because its raw request target can contain authentication values in URL
query parameters.

References:

- [Python logging configuration](https://docs.python.org/3.14/library/logging.config.html#logging.config.dictConfig)
- [Sending Amazon ECS logs to CloudWatch](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using_awslogs.html)
- [Analyzing log data with CloudWatch Logs Insights](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/AnalyzingLogData.html)
- [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
