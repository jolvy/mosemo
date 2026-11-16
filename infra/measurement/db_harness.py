"""측정 DB 하네스. 측정 Pod와 로컬 드라이런에서 psql 대신 DB에 질의한다.

모드
  sql        SQL 파일(--file) 또는 문장 하나(--query)를 실행하고 결과·실행 시간을 출력
  inventory  계정·날짜·세션 목록 JSON(k6 입력 준비가 읽는다)

`k6_inputs_and_reports.py`가 이 파일의 연결·시간 제한 함수(`Reconn`, `SQL_T`, `LONG_T`)를
import한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import ssl
import sys
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import asyncpg

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


async def init_conn(conn: asyncpg.Connection) -> None:
    for t in ("json", "jsonb"):
        await conn.set_type_codec(
            t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )


def ssl_from_env() -> ssl.SSLContext | None:
    """랩 configmap의 PGSSLMODE·PGSSLROOTCERT를 따른다(RDS는 verify-full).

    없으면 asyncpg 기본.
    """
    mode = os.environ.get("PGSSLMODE")
    if mode in (None, "", "disable", "allow", "prefer"):
        return None
    ctx = ssl.create_default_context(cafile=os.environ.get("PGSSLROOTCERT"))
    if mode != "verify-full":
        ctx.check_hostname = False
        if mode == "require":
            ctx.verify_mode = ssl.CERT_NONE
    return ctx


SSL = ssl_from_env()


# 시간 제한(도구 2판, 2026-11-14). 1차 측정에서 RDS가 재시작될 때 RST를 받지 못한 연결에서
# 하네스가 무한정 멈췄다. 단위 작업마다 상한을 두고, 예외가 나면 연결을 terminate한다.
REQ_T = float(os.environ.get("S5_REQ_TIMEOUT", "30"))  # 요청 하나(조회·쓰기)
LONG_T = float(os.environ.get("S5_LONG_TIMEOUT", "120"))  # 긴 단위 작업 하나
# sql 모드 문장 하나(VACUUM 등은 db-measurement-steps.sh가 키운다)
SQL_T = float(os.environ.get("S5_SQL_TIMEOUT", "600"))
# 연결·DB 오류. 재연결·정리는 이것만 다루고 예상하지 못한 오류에서는 멈춘다.
CONN_ERRORS = (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError)


async def close_conn(conn: asyncpg.Connection) -> None:
    try:
        await asyncio.wait_for(conn.close(), REQ_T)
    except CONN_ERRORS:
        conn.terminate()


class Reconn:
    """단일 연결 모드용. 단위 작업마다 상한을 두고, 실패하면 연결을 버리고 다음 단위에서
    다시 연결한다."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.conn: asyncpg.Connection | None = None

    async def run[T](
        self, fn: Callable[[asyncpg.Connection], Awaitable[T]], t: float = LONG_T
    ) -> T:
        if self.conn is None or self.conn.is_closed():
            self.conn = await connect(self.dsn)
        try:
            async with asyncio.timeout(t):
                return await fn(self.conn)
        except BaseException:
            self.drop()
            raise

    def drop(self) -> None:
        if self.conn is not None:
            self.conn.terminate()
            self.conn = None

    async def close(self) -> None:
        if self.conn is not None:
            await close_conn(self.conn)
            self.conn = None


async def connect(dsn: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(dsn, ssl=SSL, timeout=REQ_T)
    try:
        await asyncio.wait_for(init_conn(conn), REQ_T)
    except BaseException:
        conn.terminate()
        raise
    return conn


# --------------------------------------------------------------------------- inventory


async def account_dates(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """계정마다 구간이 있는 지역 날짜 목록."""
    return await conn.fetch("""
        SELECT id, timezone, array_agg(d ORDER BY d) AS dates FROM (
          SELECT DISTINCT a.id, a.timezone,
                 (s.started_at AT TIME ZONE a.timezone)::date AS d
          FROM accounts a JOIN activity_timeline_segments s ON s.account_id = a.id) x
        GROUP BY id, timezone ORDER BY id""")


async def cmd_inventory(a: argparse.Namespace) -> None:
    conn = await connect(a.dsn)
    async with asyncio.timeout(SQL_T):
        accs = await account_dates(conn)
        sess = await conn.fetch("""
            SELECT fs.id, fs.account_id,
                   (fs.started_at AT TIME ZONE a.timezone)::date AS d
            FROM focus_sessions fs JOIN accounts a ON a.id = fs.account_id
            WHERE fs.ended_at IS NOT NULL""")
    await close_conn(conn)
    by: dict[Any, list[dict[str, str]]] = {}
    for s in sess:
        by.setdefault(s["account_id"], []).append(
            {"id": str(s["id"]), "local_date": s["d"].isoformat()}
        )
    today = a.today or max(max(r["dates"]) for r in accs).isoformat()
    out = {
        "today": today,
        "accounts": [
            {
                "account_id": str(r["id"]),
                "timezone": r["timezone"],
                "dates": [d.isoformat() for d in r["dates"]],
                "sessions": by.get(r["id"], []),
            }
            for r in accs
        ],
    }
    await asyncio.to_thread(Path(a.out).write_text, json.dumps(out, indent=1))
    print(f"{a.out}: 계정 {len(accs)}개, today {today}")


# --------------------------------------------------------------------------- sql


def _sql_line(raw: str, vars_: dict[str, str]) -> str:
    if raw.lstrip().startswith("\\"):
        return raw.strip()
    line = raw.split("--", 1)[0].rstrip()
    for k, v in vars_.items():
        line = line.replace(f":'{k}'", "'" + v.replace("'", "''") + "'")
        # ::date 같은 형 변환은 건드리지 않는다
        line = re.sub(rf"(?<!:):{k}\b", v, line)
    return line


def sql_statements(text: str, vars_: dict[str, str]) -> list[str]:
    """이 폴더 SQL 파일용 최소 실행기.

    `--` 주석, psql 변수(:'x' :x), \\echo, 줄 끝 `;`만 다룬다.
    \\set ON_ERROR_STOP·\\timing은 무시한다(늘 오류에서 멈추고 늘 시간을 찍는다).
    """
    out: list[str] = []
    buf: list[str] = []
    for raw in text.splitlines():
        line = _sql_line(raw, vars_)
        if line.startswith("\\echo"):
            out.append(line)
            continue
        if line.startswith("\\"):
            continue
        if line:
            buf.append(line)
        if line.endswith(";"):
            out.append("\n".join(buf))
            buf = []
    if buf:
        out.append("\n".join(buf))
    return out


ROW_RETURNING = ("SELECT", "WITH", "EXPLAIN", "SHOW", "VALUES", "TABLE")
SQL_ERROR_EXIT = 3
ECHO_PREFIX_LEN = len("\\echo ")
ERROR_STATEMENT_CHARS = 200


def print_rows(rows: list[asyncpg.Record]) -> None:
    cols = list(rows[0].keys())
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join("" if r[c] is None else str(r[c]) for c in cols))


async def run_statement(
    conn: asyncpg.Connection, st: str
) -> tuple[list[asyncpg.Record], str | None, str]:
    head = st.split(None, 1)[0].upper()
    returns_rows = head in ROW_RETURNING
    try:
        async with asyncio.timeout(SQL_T):
            rows = await conn.fetch(st) if returns_rows else []
            status = None if returns_rows else await conn.execute(st)
    except CONN_ERRORS as exc:
        print(
            f"ERROR: {type(exc).__name__}: {exc} (상한 {SQL_T:.0f}s)\n"
            f"문장: {st[:ERROR_STATEMENT_CHARS]}",
            flush=True,
        )
        conn.terminate()
        sys.exit(SQL_ERROR_EXIT)
    return rows, status, head


async def cmd_sql(a: argparse.Namespace) -> None:
    vars_ = dict(v.split("=", 1) for v in a.var)
    if a.file:
        text = await asyncio.to_thread(Path(a.file).read_text, encoding="utf-8")
        stmts = sql_statements(text, vars_)
    else:
        stmts = [a.query]
    conn = await connect(a.dsn)
    for st in stmts:
        if st.startswith("\\echo"):
            print(st[ECHO_PREFIX_LEN:].strip(), flush=True)
            continue
        t0 = perf_counter()
        rows, status, head = await run_statement(conn, st)
        ms = 1000 * (perf_counter() - t0)
        if rows:
            print_rows(rows)
        print(f"-- {status or f'{head} {len(rows)}행'}, {ms:.1f} ms", flush=True)
    await close_conn(conn)


# --------------------------------------------------------------------------- main


def env_dsn() -> str | None:
    if not os.environ.get("DB_HOST"):
        return None
    user = quote(os.environ["DB_USER"])
    password = quote(os.environ.get("DB_PASSWORD", ""))
    host = os.environ["DB_HOST"]
    port = os.environ.get("DB_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{os.environ['DB_DATABASE']}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--dsn", default=os.environ.get("S5_DSN") or env_dsn())
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sql")
    s.add_argument("--file")
    s.add_argument("--query")
    s.add_argument(
        "-v",
        "--var",
        action="append",
        default=[],
        help="이름=값(psql -v와 같은 뜻)",
    )
    s = sub.add_parser("inventory")
    s.add_argument("--out", required=True)
    s.add_argument("--today")
    return p


def register_rss_log(rss_log: str, cmd: str) -> None:
    """측정 Pod 메모리 상한(768Mi) 판단용: 모드별 최대 RSS를 기록한다."""
    import atexit
    import resource

    def _rss() -> None:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS 바이트, Linux kB
        mb = peak / 1048576 if sys.platform == "darwin" else peak / 1024
        args = " ".join(" ".join(sys.argv[2:]).split())[:120]
        with Path(rss_log).open("a") as f:
            f.write(f"{cmd}\t{mb:.1f}\t{args}\n")

    atexit.register(_rss)


COMMANDS = {"sql": cmd_sql, "inventory": cmd_inventory}
# 단위가 짧은 읽기 전용 모드는 전체에 상한을 둔다
WHOLE_TIMEOUT_COMMANDS = {"inventory"}


def main() -> None:
    p = build_parser()
    a = p.parse_args()
    if not a.dsn:
        p.error("--dsn 또는 S5_DSN이 필요하다")
    rss_log = os.environ.get("S5_RSS_LOG")
    if rss_log:
        register_rss_log(rss_log, a.cmd)
    coro = COMMANDS[a.cmd](a)
    if a.cmd in WHOLE_TIMEOUT_COMMANDS:
        coro = asyncio.wait_for(coro, float(os.environ.get("S5_CMD_TIMEOUT", "1800")))
    asyncio.run(coro)


if __name__ == "__main__":
    main()
