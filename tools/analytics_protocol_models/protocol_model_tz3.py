"""4B 후속 F-2 2차: 결정 (a)를 날짜 3개로 검사(protocol_model_tz.py를 복사해 날짜 수를 일반화, 표준 라이브러리만).

결정 (a): 시간대 변경 때 (저장 날짜 ∪ 표시 날짜)와 각각의 앞뒤 하루에 표시. 아래 원래 설명은 1차 모델의 것이다.


대상 설계(사용자 결정 2026-11-13): 계정 시간대를 바꾸면 지난 날짜도 새 시간대로 다시 계산한다.
  - 일간 통계 저장 행에 계산 때의 시간대(tz)를 둔다.
  - 조회는 저장 행의 tz가 계정의 현재 tz와 다르면 원천에서 계산한다(정의 version과 같은 규칙).
  - 시간대 변경 트랜잭션은 계정 advisory 잠금 안에서 tz를 바꾸고, 그 계정의 저장 날짜마다 표시를 추가한다
    (정의 변경 mark와 같은 방식).
  - 배치 저장 트랜잭션은 정의 행처럼 계정 행의 tz를 FOR SHARE로 확인하고, 배치가 읽은 tz와 다르면 롤백한다.
나머지는 STEP4-D-REPORT.md의 V5 그대로다(전역 시퀀스 version 표시, 같은 version일 때만 삭제, sv가 클 때만 교체,
잠금 없는 선점, 선점 뒤 READ COMMITTED 문장별 원천 읽기, 저장·삭제 한 트랜잭션, 조회는 한 문장 + 한 스냅샷).

모델 규모: 계정 1개, 시간대 2값(T1→T2 변경 1회), 날짜 2개(d1, d2), 원천 부분 2~3개, 원천 변경 0~2회,
배치 2개, 조회 1개(날짜는 조회가 고름), 정의 변경 0회 또는 1회.

날짜와 원천: 원천은 부분 p, q, r(각 부분 = 같은 시각 범위에 있는 구간·라벨, 값 = 변경 횟수)이다. 시간대가 정하는
날짜 배치(layout)는 시나리오가 정한다. 예 LA: T1에서 d1 = {p, q}, d2 = {}; T2에서 d1 = {p}, d2 = {q}.
즉 부분 q는 시간대를 바꾸면 다른 날짜로 옮겨 간다(자정 근처의 활동). 이것이 "같은 원천이 다른 날짜로 나뉨"을
표현하는 최소 단위다(STEP4B-FINAL 9.2 F-2).

계산: f(def, tz, d, src) = (def, tz, d, ((부분, 값), …)) — 그 tz에서 d에 속한 부분만. 값에 어디서 왔는지가 드러난다.

원자 단계(PG 대응은 STEP4B-F2-REPORT.md 3절):
  원천 k   S.draw  advisory 잠금 + nextval + handler가 계정 tz를 읽음(어느 날짜를 표시할지 정함)
           S.commit 부분 +1, 그 tz에서 부분이 속한 날짜에 표시 UPSERT(EXCLUDED = 뽑은 version), 커밋
  시간대   T.*     변형에 따라: 잠금 → (대상 날짜 읽기) → tz UPDATE + 대상 날짜 표시 UPSERT 커밋 등
  정의     D.bump → D.lock → D.commit(옛 정의 행 날짜에 표시)
  배치 i   B.start(코드 정의, [tz 읽기]) → B.claim(표시 있는 날짜 하나를 고르고 version 기억)
           → [B.tz 읽기] → B.read(그 tz에서 날짜의 부분을 하나씩, READ COMMITTED) → B.final(정의·tz 확인, 저장 guard,
           표시 조건부 삭제, 커밋) 또는 B.abort
  조회     Q.start(날짜 선택, 계정 tz·표시·저장 행을 한 문장) → 필요하면 Q.calc(원천 한 스냅샷)

불변식(I1~I5는 STEP4-D-REPORT.md 3.4와 같은 뜻, 시간대를 넣어 정밀화):
  I1  조회 결과 = 조회 코드 정의로, 조회가 걸친 어느 한 순간 t의 (계정 tz_t, 원천_t)로 처음부터 계산한 값.
      J: 어느 상태에서든 날짜마다 한 스냅샷 조회 결과 = 그 상태의 tz·원천 계산값.
  I2  모든 프로세스가 끝난 뒤 배치를 혼자 최대 2번 더 돌리면 표시 0개, 저장 행이 있는 날짜는 저장 행 = 현재
      tz·현재 정의·현재 원천 계산값.
  I3  조회 결과의 정의 = 조회 코드의 정의.
  I4  원천 변경 커밋은 배치가 계산 동안 쥔 잠금을 기다리지 않는다(이 모델의 배치는 계산 중 잠금이 없어 구조로 성립).
  I5  저장 행의 정의 version은 줄지 않는다.
  새 불변식
  I8  I2 상태에서 현재 tz로 활동(부분)이 있는 날짜는 저장 행이 있다(ADR 0016 "활동이 있었던 날은 모두 … 표시를
      통해 계산된다"). 없으면 그 날짜는 영원히 조회 때 계산한다(결과는 맞지만 저장의 목적이 사라짐).
  I9  저장 행의 tz는 현재(새) tz가 된 뒤 옛 tz로 되돌아가지 않는다(I5의 tz판).

실행(저장소 루트에서):
  python -m tools.analytics_protocol_models.protocol_model_tz3  # 모든 변형, 요약표
  python -m tools.analytics_protocol_models.protocol_model_tz3 A0 As --trace  # 일부 변형, 위반 반례 출력
  python -m tools.analytics_protocol_models.protocol_model_tz3 --small  # CI용 작은 시나리오
변형마다 결과가 EXPECTED와 다르면 종료 코드 1로 끝난다.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any

type State = tuple[Any, ...]

DATES = ("d1", "d2", "d3")
PARTS = ("p", "q", "r")
PI = {p: i for i, p in enumerate(PARTS)}
T1, T2 = 1, 2
NEW_DEF = 2  # 정의 변경 뒤의 정의 version
SOLO_RUNS = 2

# ---------------------------------------------------------------- 변형


@dataclass(frozen=True)
class Variant:
    name: str
    note: str
    tzcheck: bool = True  # 저장 트랜잭션에서 계정 tz FOR SHARE 확인, 다르면 롤백
    tzpos: str = "start"  # 배치가 tz를 읽는 때: start(선점 전) | claim(선점 뒤)
    target: str = "stored"  # 시간대 변경이 표시할 날짜: stored(저장 행 있는 날짜) | active(새 tz로 활동 있는 날짜) | union | all
    tzmode: str = "same"  # same: tz UPDATE와 표시를 한 트랜잭션 | mark_first: 표시 커밋 뒤 tz 커밋 | tz_first: tz 커밋 뒤 표시 커밋 | none: 표시 안 함
    tzlock: bool = True  # 시간대 변경 트랜잭션이 계정 advisory 잠금을 잡는가
    tread: str = "commit"  # 대상 날짜를 읽는 때: commit(tz UPDATE 뒤, 커밋과 한 단계) | before(tz UPDATE 전 별도 단계)
    htz: str = "lock"  # 원천 handler가 tz를 읽는 때: lock(잠금 뒤) | pre(잠금 전, 같은 트랜잭션 앞 문장)
    qtz: str = (
        "rr"  # 조회: rr(tz·표시·저장 행·원천을 REPEATABLE READ 한 스냅샷, D-Q3 결정) |
    )
    # join(tz·표시·저장 한 문장, 원천은 다음 스냅샷 — D 모델의 조회) |
    # sep(tz를 먼저 별도 문장, 그다음 표시·저장, 원천) | ignore(rr이지만 저장 행 tz를 보지 않음)


@dataclass(frozen=True)
class Layout:
    name: str
    t1: tuple  # 날짜별 부분 튜플(T1)
    t2: tuple  # (T2)
    note: str


LA = Layout(
    "LA",
    (("p", "q"), ()),
    (("p",), ("q",)),
    "T1: d1={p,q} d2={} / T2: d1={p} d2={q} (빈 날짜로 옮김)",
)
LB = Layout(
    "LB",
    (("p", "q"), ("r",)),
    (("p",), ("q", "r")),
    "T1: d1={p,q} d2={r} / T2: d1={p} d2={q,r} (저장 날짜 사이)",
)
LC = Layout(
    "LC",
    (("p",), ("q",)),
    (("p", "q"), ()),
    "T1: d1={p} d2={q} / T2: d1={p,q} d2={} (d2가 비게 됨)",
)


def parts_of(lay: Layout, tz: int, *, di: int) -> tuple[str, ...]:
    return (lay.t1 if tz == T1 else lay.t2)[di]


def date_of(lay: Layout, tz: int, *, part: str) -> int:
    for di in range(len(lay.t1)):
        if part in parts_of(lay, tz, di=di):
            return di
    raise ValueError(part)


@dataclass(frozen=True)
class Scenario:
    lay: Layout
    parts: tuple  # 원천 변경이 바꾸는 부분 순서
    init: str = "stored"  # stored: T1 활동 날짜마다 저장 행 | marker: pend 날짜는 저장 행 없이 표시 1(첫 저장 대기)
    pend: int = 0  # init=marker일 때 표시만 있는 날짜 번호
    def_change: bool = False

    def label(self):
        return (
            f"{self.lay.name}·초기 {self.init}·변경 {'→'.join(self.parts) or '없음'}"
            f"{'·정의변경' if self.def_change else ''}"
        )


def f(dfn: int, tz: int, *, di: int, src: Any, lay: Layout) -> tuple[Any, ...]:
    return (
        dfn,
        tz,
        DATES[di],
        tuple((p, src[PI[p]]) for p in parts_of(lay, tz, di=di)),
    )


# ---------------------------------------------------------------- 상태
# 전역 g = (src, seq, tz, mk, st, dfn, adv)
#   src: 부분별 값 튜플, mk: 날짜별 None|version, st: 날짜별 None|(def, tz, sv, val), adv: 잠금 보유자
SRC, SEQ, TZ, MK, ST, DEF, ADV = range(7)
NG = 7
# 프로세스: srcp, tzp, defp, b1, b2, q  → 인덱스 NG..NG+5
P_SRC, P_TZ, P_DEF, P_B1, P_B2, P_Q = range(NG, NG + 6)


def initial(sc: Scenario) -> State:
    lay = sc.lay
    src = (0, 0, 0)
    n = len(lay.t1)
    mk = [None] * n
    st = [None] * n
    for di in range(n):
        if parts_of(lay, T1, di=di):
            st[di] = (1, T1, 1, f(1, T1, di=di, src=src, lay=lay))
    if sc.init == "marker":
        st[sc.pend], mk[sc.pend] = None, 1
    g = (src, 2, T1, tuple(mk), tuple(st), 1, None)
    srcp = ("draw", 0, None, None) if sc.parts else ("done", 0, None, None)
    tzp = ("start", None)
    defp = ("bump",) if sc.def_change else ("done",)
    batch = ("start", None, None, None, None, ())  # (pc, mydef, mytz, di, cv, reads)
    q = (
        "idle",
        None,
        None,
        None,
        None,
        None,
        frozenset(),
    )  # (pc, di, qdef, qtz, mrk, row, valid)
    return g + (srcp, tzp, defp, batch, batch, q)


def usable(v: Variant, mrk: Any, *, row: Any, qdef: int, qtz: int) -> bool:
    if mrk is not None or row is None or row[0] != qdef:
        return False
    return v.qtz == "ignore" or row[1] == qtz


def atomic_query(v: Variant, g: Any, *, di: int, lay: Layout) -> Any:
    mrk, row = g[MK][di], g[ST][di]
    if usable(v, mrk, row=row, qdef=g[DEF], qtz=g[TZ]):
        return row[3]
    return f(g[DEF], g[TZ], di=di, src=g[SRC], lay=lay)


def targets(v: Variant, g: Any, *, lay: Layout, newtz: int) -> frozenset[int]:
    n = len(g[MK])
    stored = {di for di in range(n) if g[ST][di] is not None}
    marked = {di for di in range(n) if g[MK][di] is not None}
    active = {di for di in range(n) if parts_of(lay, newtz, di=di)}

    def around(base, k):
        return {d + o for d in base for o in range(-k, k + 1) if 0 <= d + o < n}

    return frozenset(
        {
            "stored": stored,
            "active": active,
            "union": stored | active,
            "all": set(range(n)),
            "adj": around(stored | marked, 1),  # 결정 (a): (저장 ∪ 표시) ±1일
            "adj2": around(stored | marked, 2),  # ±2일
            "adj_stored": around(stored, 1),  # 저장 날짜만 ±1일(표시 날짜 빠짐)
        }[v.target]
    )


def mark(g: list[Any], dates: Any) -> None:
    """잠금 안에서 날짜마다 새 nextval로 표시 UPSERT(잠금이 있으면 version은 커밋 순서대로 커진다)."""
    mk = list(g[MK])
    seq = g[SEQ]
    for di in sorted(dates):
        mk[di] = seq
        seq += 1
    g[MK] = tuple(mk)
    g[SEQ] = seq


def setp(tup: Any, i: int, *, val: Any) -> tuple[Any, ...]:
    lst = list(tup)
    lst[i] = val
    return tuple(lst)


# ---------------------------------------------------------------- 전이


def successors(v: Variant, sc: Scenario, *, s: State) -> list[Any]:
    lay = sc.lay
    out = []
    procs = list(s[NG:])

    def emit(label: str, gl: list[Any], *, newprocs: list[Any], viol: Any = ()) -> None:
        g = tuple(gl)
        ns = g + tuple(newprocs)
        qq = ns[P_Q]
        if qq[0] not in ("idle", "done"):
            valid = qq[6] | {f(qq[2], g[TZ], di=qq[1], src=g[SRC], lay=lay)}
            ns = ns[:P_Q] + (qq[:6] + (valid,),)
        found: list[str] = list(viol)
        for di in range(len(g[ST])):
            a, b = s[ST][di], g[ST][di]
            if a is not None and b is not None:
                if b[0] < a[0]:
                    found.append("I5")
                if a[1] == g[TZ] and b[1] != g[TZ]:
                    found.append("I9")
        out.append((label, ns, found))

    def copy_globals() -> list[Any]:
        return list(s[:NG])

    def with_proc(i: int, val: Any) -> list[Any]:
        p = list(procs)
        p[i - NG] = val
        return p

    # 원천 변경(계정 advisory 잠금으로 직렬)
    pc, k, drawn, htz = s[P_SRC]
    if pc == "draw" and v.htz == "pre" and htz is None:
        emit(
            f"원천{k + 1} 잠금 전에 tz=T{s[TZ]} 읽음",
            copy_globals(),
            newprocs=with_proc(P_SRC, ("draw", k, None, s[TZ])),
        )
    elif pc == "draw" and s[ADV] is None:
        g = copy_globals()
        val = g[SEQ]
        g[SEQ] += 1
        g[ADV] = "S"
        rtz = htz if v.htz == "pre" else s[TZ]
        emit(
            f"원천{k + 1} 시작: 잠금, nextval={val}"
            + ("" if v.htz == "pre" else f", handler가 tz=T{s[TZ]} 읽음"),
            g,
            newprocs=with_proc(P_SRC, ("commit", k, val, rtz)),
        )
    elif pc == "commit":
        g = copy_globals()
        part = sc.parts[k]
        src = list(g[SRC])
        src[PI[part]] += 1
        g[SRC] = tuple(src)
        di = date_of(lay, htz, part=part)
        g[MK] = setp(g[MK], di, val=drawn)
        g[ADV] = None
        nxt = (
            ("draw", k + 1, None, None)
            if k + 1 < len(sc.parts)
            else ("done", k + 1, None, None)
        )
        emit(
            f"원천{k + 1} 커밋: {part} +1, 표시 {DATES[di]}={drawn} (T{htz} 기준 날짜)",
            g,
            newprocs=with_proc(P_SRC, nxt),
        )

    # 시간대 변경
    tpc, tt = s[P_TZ]
    adv_free = s[ADV] is None
    if v.tzmode == "same":
        if tpc == "start" and (adv_free or not v.tzlock):
            g = copy_globals()
            if v.tzlock:
                g[ADV] = "T"
            nxt = "targets" if v.tread == "before" else "commit"
            emit(
                f"시간대 변경 시작{': 잠금' if v.tzlock else ''}",
                g,
                newprocs=with_proc(P_TZ, (nxt, None)),
            )
        elif tpc == "targets":
            tg = targets(v, s, lay=lay, newtz=T2)
            emit(
                f"시간대 변경: 대상 날짜 읽기(tz UPDATE 전) {[DATES[d] for d in sorted(tg)]}",
                copy_globals(),
                newprocs=with_proc(P_TZ, ("commit", tg)),
            )
        elif tpc == "commit":
            g = copy_globals()
            g[TZ] = T2
            tg = tt if v.tread == "before" else targets(v, g, lay=lay, newtz=T2)
            mark(g, tg)
            if v.tzlock:
                g[ADV] = None
            emit(
                f"시간대 변경 커밋: tz=T2, 표시 {[DATES[d] for d in sorted(tg)]} → 표시={g[MK]}",
                g,
                newprocs=with_proc(P_TZ, ("done", None)),
            )
    elif v.tzmode == "none":
        if tpc == "start":
            g = copy_globals()
            g[TZ] = T2
            emit(
                "시간대 변경 커밋: tz=T2(표시 없음)",
                g,
                newprocs=with_proc(P_TZ, ("done", None)),
            )
    elif v.tzmode == "mark_first":
        if tpc == "start" and adv_free:
            g = copy_globals()
            g[ADV] = "T"
            emit(
                "시간대 표시 트랜잭션 시작: 잠금",
                g,
                newprocs=with_proc(P_TZ, ("mark", None)),
            )
        elif tpc == "mark":
            g = copy_globals()
            tg = targets(v, g, lay=lay, newtz=T2)
            mark(g, tg)
            g[ADV] = None
            emit(
                f"시간대 표시 커밋(tz는 아직 T1): 표시={g[MK]}",
                g,
                newprocs=with_proc(P_TZ, ("update", None)),
            )
        elif tpc == "update":
            g = copy_globals()
            g[TZ] = T2
            emit(
                "시간대 UPDATE 커밋: tz=T2", g, newprocs=with_proc(P_TZ, ("done", None))
            )
    elif v.tzmode == "tz_first":
        if tpc == "start" and v.tzlock and adv_free:
            g = copy_globals()
            g[ADV] = "T"
            emit(
                "시간대 UPDATE 트랜잭션 시작: 잠금",
                g,
                newprocs=with_proc(P_TZ, ("update", None)),
            )
        elif (tpc == "start" and not v.tzlock) or tpc == "update":
            g = copy_globals()
            g[TZ] = T2
            if tpc == "update":
                g[ADV] = None
            emit(
                "시간대 UPDATE 커밋: tz=T2(표시는 아직)",
                g,
                newprocs=with_proc(P_TZ, ("lock", None)),
            )
        elif tpc == "lock" and adv_free:
            g = copy_globals()
            g[ADV] = "T"
            emit(
                "시간대 표시 트랜잭션 시작: 잠금",
                g,
                newprocs=with_proc(P_TZ, ("mark", None)),
            )
        elif tpc == "mark":
            g = copy_globals()
            tg = targets(v, g, lay=lay, newtz=T2)
            mark(g, tg)
            g[ADV] = None
            emit(
                f"시간대 표시 커밋: 표시={g[MK]}",
                g,
                newprocs=with_proc(P_TZ, ("done", None)),
            )

    # 정의 변경(D의 V5 mark)
    dpc = s[P_DEF][0]
    if dpc == "bump":
        g = copy_globals()
        g[DEF] = NEW_DEF
        emit("정의 version 1→2", g, newprocs=with_proc(P_DEF, ("lock",)))
    elif dpc == "lock" and adv_free:
        g = copy_globals()
        g[ADV] = "D"
        emit("정의 재처리 표시 시작: 잠금", g, newprocs=with_proc(P_DEF, ("commit",)))
    elif dpc == "commit":
        g = copy_globals()
        tg = {
            di
            for di in range(len(g[ST]))
            if g[ST][di] is not None and g[ST][di][0] < NEW_DEF
        }
        mark(g, tg)
        g[ADV] = None
        emit(
            f"정의 재처리 표시 커밋: 표시={g[MK]}",
            g,
            newprocs=with_proc(P_DEF, ("done",)),
        )

    # 배치
    for idx in (P_B1, P_B2):
        bi = idx - P_B1 + 1
        bpc, mydef, mytz, di, cv, reads = s[idx]
        name = f"배치{bi}"

        def put(
            local: Any,
            g: list[Any],
            *,
            label: str,
            viol: Any = (),
            slot: tuple[int, str] = (idx, name),
        ) -> None:
            emit(f"{slot[1]} {label}", g, newprocs=with_proc(slot[0], local), viol=viol)

        if bpc == "start":
            tz = s[TZ] if v.tzpos == "start" else None
            put(
                ("claim", s[DEF], tz, None, None, ()),
                copy_globals(),
                label=f"시작: 코드 정의={s[DEF]}" + (f", tz=T{tz} 읽음" if tz else ""),
            )
        elif bpc == "claim":
            any_mark = False
            for d in range(len(s[MK])):
                if s[MK][d] is not None:
                    any_mark = True
                    nxt = "tz" if v.tzpos == "claim" else "read"
                    put(
                        (nxt, mydef, mytz, d, s[MK][d], ()),
                        copy_globals(),
                        label=f"선점: {DATES[d]} 표시={s[MK][d]}",
                    )
            if not any_mark:
                put(
                    ("done", mydef, None, None, None, ()),
                    copy_globals(),
                    label="선점: 일 없음",
                )
        elif bpc == "tz":
            put(
                ("read", mydef, s[TZ], di, cv, ()),
                copy_globals(),
                label=f"tz=T{s[TZ]} 읽음",
            )
        elif bpc == "read":
            want = parts_of(lay, mytz, di=di)
            if len(reads) < len(want):
                p = want[len(reads)]
                nreads = reads + ((p, s[SRC][PI[p]]),)
                nxt = "read" if len(nreads) < len(want) else "final"
                put(
                    (nxt, mydef, mytz, di, cv, nreads),
                    copy_globals(),
                    label=f"원천 읽기 {p}={s[SRC][PI[p]]} (T{mytz}의 {DATES[di]})",
                )
            else:
                put(
                    ("final", mydef, mytz, di, cv, reads),
                    copy_globals(),
                    label=f"원천 읽기: T{mytz}의 {DATES[di]}에 부분 없음",
                )
        elif bpc == "final":
            put(
                ("done", mydef, None, None, None, ()),
                copy_globals(),
                label="실패: 롤백",
            )
            if s[DEF] != mydef or (v.tzcheck and s[TZ] != mytz):
                continue  # 확인 실패 롤백은 위 롤백과 같다
            g = copy_globals()
            val = (mydef, mytz, DATES[di], reads)
            row = g[ST][di]
            if row is None or cv > row[2]:
                g[ST] = setp(g[ST], di, val=(mydef, mytz, cv, val))
            if g[MK][di] == cv:
                g[MK] = setp(g[MK], di, val=None)
            put(
                ("done", mydef, None, None, None, ()),
                g,
                label=f"저장+표시 삭제 커밋: {DATES[di]} 계산값={val}, sv={cv} → 저장={g[ST][di]}, 표시={g[MK][di]}",
            )

    # 조회
    qpc, qdi, qdef, qtz, qm, qrow, valid = s[P_Q]

    def qfinish(res: Any, label: str, *, here_valid: Any) -> None:
        viol = []
        if res[0] != qdef:
            viol.append("I3")
        elif res not in here_valid:
            viol.append("I1")
        emit(
            f"조회 {label} → 결과 {res}",
            copy_globals(),
            newprocs=with_proc(P_Q, ("done", qdi, qdef, qtz, qm, qrow, here_valid)),
            viol=viol,
        )

    if qpc == "idle":
        for d in range(len(s[MK])):
            start_valid = frozenset({f(s[DEF], s[TZ], di=d, src=s[SRC], lay=lay)})
            if v.qtz in ("rr", "ignore"):
                m, row = s[MK][d], s[ST][d]
                res = atomic_query(v, s, di=d, lay=lay)
                how = (
                    "저장 행 사용"
                    if usable(v, m, row=row, qdef=s[DEF], qtz=s[TZ])
                    else "조회 때 계산"
                )
                emit(
                    f"조회({DATES[d]}) 한 스냅샷: tz=T{s[TZ]}, 표시={m}, 저장={row} → {how} → 결과 {res}",
                    copy_globals(),
                    newprocs=with_proc(
                        P_Q, ("done", d, s[DEF], s[TZ], m, row, start_valid)
                    ),
                    viol=[] if res in start_valid else ["I1"],
                )
                continue
            if v.qtz == "sep":
                emit(
                    f"조회({DATES[d]}) 계정 tz 읽기: T{s[TZ]}",
                    copy_globals(),
                    newprocs=with_proc(
                        P_Q, ("q2", d, s[DEF], s[TZ], None, None, start_valid)
                    ),
                )
                continue
            m, row = s[MK][d], s[ST][d]
            if usable(v, m, row=row, qdef=s[DEF], qtz=s[TZ]):
                res = row[3]
                viol = [] if res in start_valid else ["I1"]
                emit(
                    f"조회({DATES[d]}) tz·표시·저장 한 문장: tz=T{s[TZ]}, 표시={m}, 저장={row} → 결과 {res}",
                    copy_globals(),
                    newprocs=with_proc(
                        P_Q, ("done", d, s[DEF], s[TZ], m, row, start_valid)
                    ),
                    viol=viol,
                )
            else:
                emit(
                    f"조회({DATES[d]}) tz·표시·저장 한 문장: tz=T{s[TZ]}, 표시={m}, 저장={row} → 조회 때 계산",
                    copy_globals(),
                    newprocs=with_proc(
                        P_Q, ("calc", d, s[DEF], s[TZ], m, row, start_valid)
                    ),
                )
    elif qpc == "q2":
        m, row = s[MK][qdi], s[ST][qdi]
        here = valid | {f(qdef, s[TZ], di=qdi, src=s[SRC], lay=lay)}
        if usable(v, m, row=row, qdef=qdef, qtz=qtz):
            qfinish(row[3], f"표시·저장 읽기: 표시={m}, 저장={row}", here_valid=here)
        else:
            emit(
                f"조회 표시·저장 읽기: 표시={m}, 저장={row} → 조회 때 계산",
                copy_globals(),
                newprocs=with_proc(P_Q, ("calc", qdi, qdef, qtz, m, row, valid)),
            )
    elif qpc == "calc":
        here = valid | {f(qdef, s[TZ], di=qdi, src=s[SRC], lay=lay)}
        qfinish(
            f(qdef, qtz, di=qdi, src=s[SRC], lay=lay),
            f"원천 읽기(한 스냅샷, T{qtz})",
            here_valid=here,
        )
    return out


def check_state(v: Variant, sc: Scenario, *, s: State) -> list[str]:
    for di in range(len(s[MK])):
        if atomic_query(v, s, di=di, lay=sc.lay) != f(
            s[DEF], s[TZ], di=di, src=s[SRC], lay=sc.lay
        ):
            return ["I1"]  # J
    return []


def is_terminal(s: State) -> bool:
    return all(p[0] == "done" for p in s[NG:])


def solo_batch(state: State, lay: Layout) -> tuple[State, bool]:
    """변경이 멈춘 뒤 혼자 도는 배치 1회: 표시 있는 날짜를 모두 현재 정의·tz로 처리."""
    g = list(state)
    worked = False
    for di in range(len(g[MK])):
        cv = g[MK][di]
        if cv is None:
            continue
        worked = True
        val = f(g[DEF], g[TZ], di=di, src=g[SRC], lay=lay)
        row = g[ST][di]
        if row is None or cv > row[2]:
            g[ST] = setp(g[ST], di, val=(g[DEF], g[TZ], cv, val))
        g[MK] = setp(g[MK], di, val=None)
    return tuple(g), worked


def rows_ok(g: State, lay: Layout) -> bool:
    if any(m is not None for m in g[MK]):
        return False
    for di in range(len(g[MK])):
        row = g[ST][di]
        if row is not None and not (
            row[0] == g[DEF]
            and row[1] == g[TZ]
            and row[3] == f(g[DEF], g[TZ], di=di, src=g[SRC], lay=lay)
        ):
            return False
    return True


def check_terminal(sc: Scenario, s: State) -> tuple[list[str], int | None]:
    g = s[:NG]
    for runs in range(SOLO_RUNS + 1):
        if rows_ok(g, sc.lay):
            viol = []
            for di in range(len(g[MK])):
                if parts_of(sc.lay, g[TZ], di=di) and g[ST][di] is None:
                    viol.append("I8")
            return viol, runs
        if runs == SOLO_RUNS:
            break
        g, worked = solo_batch(g, sc.lay)
        if not worked:
            break
    return ["I2"], None


# ---------------------------------------------------------------- 탐색


@dataclass
class Result:
    states: int = 0
    transitions: int = 0
    terminals: int = 0
    orders: int = 0
    max_depth: int = 0
    solo_runs_max: int = 0
    violations: dict[str, int] = field(default_factory=dict)
    first: dict[str, tuple[Any, Any, Any]] = field(default_factory=dict)
    parents: dict[State, Any] | None = None
    seconds: float = 0.0


def explore(v: Variant, sc: Scenario, *, keep_parents: bool = False) -> Result:
    t0 = time.perf_counter()
    init = initial(sc)
    seen = {init: None}
    depth = {init: 0}
    frontier = deque([init])
    res = Result()

    def hit(kind: str, state: State, *, via: Any = None, prev: Any = None) -> None:
        res.violations[kind] = res.violations.get(kind, 0) + 1
        if kind not in res.first:
            res.first[kind] = (state, via, prev)

    for kind in check_state(v, sc, s=init):
        hit(kind, init)
    while frontier:
        s = frontier.popleft()
        succ = successors(v, sc, s=s)
        if not succ:
            if is_terminal(s):
                res.terminals += 1
                viol, runs = check_terminal(sc, s)
                for kind in viol:
                    hit(kind, s)
                if runs is not None:
                    res.solo_runs_max = max(res.solo_runs_max, runs)
            else:
                hit("DEADLOCK", s)
            continue
        for label, ns, viol in succ:
            res.transitions += 1
            for kind in viol:
                hit(kind, ns, via=label, prev=s)
            if ns not in seen:
                seen[ns] = (s, label) if keep_parents else None
                depth[ns] = depth[s] + 1
                res.max_depth = max(res.max_depth, depth[ns])
                for kind in check_state(v, sc, s=ns):
                    hit(kind, ns)
                frontier.append(ns)
    res.states = len(seen)
    res.parents = seen if keep_parents else None

    # 실행 순서 수(시작 → 끝 경로 수). 모든 전이가 어떤 프로세스를 한 단계 진행시키므로 순환이 없다.
    memo = {}
    onstack = set()
    stack = [(init, None)]
    while stack:
        st, succ = stack.pop()
        if st in memo:
            continue
        if succ is None:
            succ = [ns for _, ns, _ in successors(v, sc, s=st)]
            onstack.add(st)
            stack.append((st, succ))
            for ns in succ:
                if ns in onstack:
                    raise RuntimeError("상태 그래프에 순환이 있다")
                if ns not in memo:
                    stack.append((ns, None))
        else:
            memo[st] = 1 if not succ else sum(memo[ns] for ns in succ)
            onstack.discard(st)
    res.orders = memo[init]
    res.seconds = time.perf_counter() - t0
    return res


def trace(res: Result, state: State) -> list[tuple[str, State]]:
    parents = res.parents
    assert parents is not None
    path = []
    while parents[state] is not None:
        prev, label = parents[state]
        path.append((label, state))
        state = prev
    path.reverse()
    return path


def show_state(s: State) -> str:
    src = ", ".join(f"{p}={s[SRC][PI[p]]}" for p in PARTS)
    rows = "; ".join(
        f"{DATES[di]} 표시={s[MK][di]} 저장={s[ST][di]}" for di in range(len(s[MK]))
    )
    return f"tz=T{s[TZ]} 정의={s[DEF]} 원천({src}) | {rows}"


# ---------------------------------------------------------------- 변형·시나리오

BASE = Variant(
    "A0", "결정 (a): 사용자 설계 + 표시 대상 = (저장 ∪ 표시) 날짜 ±1일", target="adj"
)

VARIANTS = [
    BASE,
    replace(BASE, name="Ax", note="표시 대상 = 모든 날짜", target="all"),
    replace(BASE, name="A2", note="표시 대상 = (저장 ∪ 표시) ±2일", target="adj2"),
    replace(
        BASE,
        name="As",
        note="표시 대상 = 저장 날짜만(원래 사용자 설계, F-2 1차의 Z0)",
        target="stored",
    ),
    replace(
        BASE,
        name="Ast",
        note="표시 대상 = 저장 날짜 ±1일(표시만 있는 날짜를 빼먹음)",
        target="adj_stored",
    ),
    replace(BASE, name="A0n", note="A0에서 저장 때 tz 확인 없음", tzcheck=False),
    replace(
        BASE,
        name="A0h",
        note="A0에서 handler가 tz를 잠금 전에 읽음(제약 1 위반)",
        htz="pre",
    ),
    replace(
        BASE,
        name="A0q",
        note="A0에서 조회의 tz·표시·저장과 원천이 다른 스냅샷(제약 2 위반)",
        qtz="join",
    ),
]

# 3일 배치. 지원 tz 3개(시차 ≤14h)에서는 모든 순간의 날짜가 같은 방향으로 0 또는 1일 움직인다(L3A~L3D).
# L3E는 시차가 24h를 넘는 두 tz(예 +14와 -12)를 나타낸다: q가 2일 움직인다.
L3A = Layout(
    "L3A",
    (("p", "q"), (), ()),
    (("p",), ("q",), ()),
    "+1일로 빈 날짜에 들어감, d3는 양쪽 다 비어 있음",
)
L3B = Layout(
    "L3B",
    (("p",), (), ("q", "r")),
    (("p",), ("q",), ("r",)),
    "-1일로 가운데 빈 날짜에 들어감",
)
L3C = Layout(
    "L3C", (("p",), ("q",), ("r",)), ((), ("p",), ("q", "r")), "+1일 연쇄, d1이 비게 됨"
)
L3D = Layout(
    "L3D",
    ((), ("q",), ("p",)),
    (("q",), (), ("p",)),
    "-1일, 옮겨 가는 원천의 날짜는 표시만 있고 저장 행 없음",
)
L3E = Layout(
    "L3E", (("p", "q"), (), ()), (("p",), (), ("q",)), "+2일(시차 24h 초과 tz 쌍)"
)

SCENARIOS = (
    [Scenario(L3A, p) for p in ((), ("q", "p"), ("p", "q"))]
    + [Scenario(L3B, p) for p in ((), ("q", "r"), ("r", "q"))]
    + [Scenario(L3C, p) for p in ((), ("q", "p"))]
    + [Scenario(L3D, p, init="marker", pend=1) for p in ((), ("q", "p"))]
    + [Scenario(L3E, p) for p in ((), ("q", "p"))]
    + [Scenario(L3B, ("q",), def_change=True)]
)


# 변형별 기대 결과: 위반한 불변식 집합(빈 집합 = 위반 0).
# 옮기기 전 원본의 전체 실행 출력 protocol_model_tz3_run.txt 끝 요약에서 옮겼다.
# README 표대로 A0는 대조 변형이다(시차 24시간 초과 L3E에서 I8).
EXPECTED: dict[str, frozenset[str]] = {
    "A0": frozenset({"I8"}),
    "Ax": frozenset(),
    "A2": frozenset(),
    "As": frozenset({"I8"}),
    "Ast": frozenset({"I8"}),
    "A0n": frozenset({"I2", "I8", "I9"}),
    "A0h": frozenset({"I1", "I2", "I8"}),
    "A0q": frozenset({"I1", "I8"}),
}

# CI용 작은 시나리오: 추천 Ax와 대조 As(저장 날짜만 표시 → I8)를 L3A·원천 변경 없음 하나로.
# 작은 시나리오의 기대 결과(전체 탐색의 EXPECTED와 따로 둔다). --small은 이 변형만 돌리고 이 값과 비교한다.
SMALL_EXPECTED: dict[str, frozenset[str]] = {
    "Ax": frozenset(),
    "As": frozenset({"I8"}),
}
SMALL_SCENARIOS = [Scenario(L3A, ())]


def verdict(kinds: frozenset[str]) -> str:
    return "통과" if not kinds else "실패 " + ",".join(sorted(kinds))


def print_counterexamples(v: Variant, rows: list[tuple[Scenario, Result]]) -> None:
    best: dict[str, Any] = {}
    for sc, r in rows:
        for kind, (st, via, prev) in r.first.items():
            path = [*trace(r, prev), (via, st)] if prev is not None else trace(r, st)
            if kind not in best or len(path) < len(best[kind][1]):
                best[kind] = (sc, path, st, via)
    for kind, (sc, path, st, via) in sorted(best.items()):
        print(f"  -- {kind} 가장 짧은 반례 ({sc.label()}, {len(path)}단계)")
        print(f"     0. 초기: {show_state(initial(sc))}")
        for i, (label, s2) in enumerate(path, 1):
            print(f"     {i}. {label}\n        → {show_state(s2)}")
        dates = range(len(st[MK]))
        if kind in ("I2", "I8"):
            g = st[:NG]
            for n in range(SOLO_RUNS):
                g2, worked = solo_batch(g, sc.lay)
                print(
                    f"     +{n + 1}. 추가 배치(혼자): {'처리' if worked else '일 없음'} → "
                    f"{show_state(g2 + (None,) * 6)}"
                )
                g = g2
            answers = []
            for di in dates:
                answer = f(st[DEF], st[TZ], di=di, src=st[SRC], lay=sc.lay)
                active = " (활동 있음)" if parts_of(sc.lay, st[TZ], di=di) else ""
                answers.append(f"{DATES[di]}={answer}{active}")
            print("     정답: " + "; ".join(answers))
        if kind == "I1" and via is None:
            answers = []
            for di in dates:
                got = atomic_query(v, st, di=di, lay=sc.lay)
                answer = f(st[DEF], st[TZ], di=di, src=st[SRC], lay=sc.lay)
                answers.append(f"{DATES[di]} {got} (정답 {answer})")
            print("     이 상태의 한 스냅샷 조회: " + "; ".join(answers))


def run(names: list[str], *, show_trace: bool, small: bool) -> int:
    default = list(SMALL_EXPECTED) if small else [v.name for v in VARIANTS]
    chosen = [v for v in VARIANTS if v.name in (names or default)]
    scenarios = SMALL_SCENARIOS if small else SCENARIOS
    total_states = total_orders = 0
    summary = []
    t_all = time.perf_counter()
    for v in chosen:
        agg: dict[str, int] = {}
        rows = []
        for sc in scenarios:
            r = explore(v, sc, keep_parents=show_trace)
            total_states += r.states
            total_orders += r.orders
            rows.append((sc, r))
            for k, n in r.violations.items():
                agg[k] = agg.get(k, 0) + n
        kinds = frozenset(agg)
        summary.append(
            (v, kinds, sum(r.states for _, r in rows), sum(r.orders for _, r in rows))
        )
        print(f"\n=== {v.name}: {v.note}", flush=True)
        for sc, r in rows:
            vio = (
                ", ".join(f"{k}:{n}" for k, n in sorted(r.violations.items())) or "없음"
            )
            print(
                f"  {sc.label():32s} 상태 {r.states:>8,} 전이 {r.transitions:>9,} "
                f"끝상태 {r.terminals:>6,} 실행순서 {r.orders:>18,} 깊이 {r.max_depth:>2} "
                f"추가배치≤{r.solo_runs_max} {r.seconds:6.1f}s 위반 {vio}",
                flush=True,
            )
        print(f"  판정: {verdict(kinds)}")
        if show_trace and agg:
            print_counterexamples(v, rows)
    print("\n=== 요약")
    mismatched = []
    unchecked: list[str] = []
    for v, kinds, ns, no in summary:
        expected = (SMALL_EXPECTED if small else EXPECTED).get(v.name)
        mark = ""
        if expected is None:
            mark = " (이 시나리오의 기대 결과 없음)"
            unchecked.append(v.name)
        elif kinds != expected:
            mark = f" (기대와 다름: {verdict(expected)})"
            mismatched.append(v.name)
        print(
            f"  {v.name:4s} {verdict(kinds):20s} 상태 {ns:>10,} 실행순서 {no:>22,}  {v.note}{mark}"
        )
    print(
        f"\n합계: 상태 {total_states:,}, 실행 순서 {total_orders:,}, "
        f"{time.perf_counter() - t_all:.1f}s"
    )
    if mismatched:
        print(f"기대 결과와 다른 변형: {', '.join(mismatched)}")
        return 1
    if unchecked:
        print(f"기대 결과가 없어 비교하지 않은 변형: {', '.join(unchecked)}")
    print("비교한 모든 변형이 기대 결과와 같다")
    return 0


def main(argv: list[str]) -> int:
    names = [a for a in argv if not a.startswith("--")]
    return run(names, show_trace="--trace" in argv, small="--small" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
