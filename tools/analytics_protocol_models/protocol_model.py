"""4단계 D: 일간 통계 프로토콜 상태 모델과 전수 탐색(표준 라이브러리만).

대상: ADR 0016의 지난 날짜 일간 통계 저장 프로토콜.
  원천(구간 = a, 라벨 확정 = b) 변경 handler가 같은 트랜잭션에서 (계정, 날짜) 표시를 UPSERT하고,
  배치가 표시 선점 → 원천 읽기 → 계산 → 저장 + 표시 삭제를 하고, 조회는 표시가 남았거나 옛 정의
  version이면 원천에서 계산하고 아니면 저장값을 쓴다.

규모(사전 등록 D): 원천 변경 2회, 배치 2개, 날짜 1개, 정의 version 변경 0회 또는 1회.
여기에 조회 1개를 아무 시점에나 끼운다(읽기만 하므로 다른 프로세스에 영향이 없고, 조회 1개가 모든
시점을 다 거치므로 조회 정확성 검사에 충분하다).

원자 단계(한 단계 = PostgreSQL에서 한 문장 또는 한 커밋, 아래 각 함수의 주석과 STEP4-D-REPORT.md 참조):
  원천 k(k=1,2, 계정 advisory 잠금으로 직렬화)
    S.draw   advisory 잠금 획득 + nextval로 표시 version 뽑기(트랜잭션 밖 값, PG8)
    S.commit 원천 한 부분 변경 + 표시 UPSERT를 함께 커밋(같은 트랜잭션, ADR 0012)
  정의 변경(있을 때)
    D.bump   현재 정의 version 1→2(배포 또는 정의 행 갱신)
    D.draw / D.commit  (재처리 방식이 mark일 때) 표시를 새 version으로 UPSERT
  배치 i(i=1,2)
    B.start  자기 코드의 정의 version을 정함(시작 시점의 현재 정의)
    B.claim  표시를 읽어 version v를 기억(선점). hold 변형은 FOR UPDATE SKIP LOCKED를
             트랜잭션 끝까지 유지
    B.read   원천 읽기. one = 한 스냅샷(REPEATABLE READ 또는 한 문장),
             two = a와 b를 READ COMMITTED 두 문장으로
    B.final  저장(guard) + 표시 삭제(조건) 커밋. 또는 B.abort(배치 실패)
  조회
    Q.*      표시·저장 행·현재 정의 읽기(한 문장 또는 두 문장) → 필요하면 원천 읽기(한 스냅샷 또는 두 문장)

불변식
  I1  조회 결과 = 조회 코드의 정의로, 조회가 걸친 어느 순간의 원천을 처음부터 계산한 값(선형화).
      조회 한 건은 한 코드 버전(정의 version)으로 실행되므로 정의는 조회 시작 때의 현재 정의로 고정한다.
      J: 어느 상태에서든 한 스냅샷 조회의 결과 = 그 상태의 원천으로 계산한 값(I1의 상태 형태).
  I2  모든 프로세스가 끝난 상태(변경이 멈춤)에서 새 배치를 혼자 최대 2번 더 돌리면 표시 0개, 저장 행 =
      현재 원천·현재 정의의 계산값. 정해진 배치 2개는 마지막 변경보다 먼저 끝날 수 있으므로 "다음 실행"을 둔다.
  I3  조회 결과의 정의 version = 조회 코드의 정의(다른 정의로 저장된 행을 돌려주지 않는다).
      저장 행의 값과 정의 표기는 한 번의 계산에서 함께 나오므로 행 안에서 섞이지 않는다(모델이 구조로 보장).
  I4  (추가) 원천 변경 트랜잭션은 배치가 계산 동안 쥔 잠금을 기다리지 않는다. 근거: 요구사항 NFR
      "일간 통계 배치의 실패나 지연은 … 라벨 확정 … 막지 않고", 원천 쓰기의 lock_timeout 3초(PG11).
  I5  (추가) 저장 행의 정의 version은 줄지 않는다. 근거: 기간 통계가 저장 행을 더할 때 옛 정의 행으로
      되돌아가면 I2 수렴이 다시 깨지고 재처리가 반복된다(AC-8023 "배치가 새 정의로 다시 저장").

실행(저장소 루트에서):
  python -m tools.analytics_protocol_models.protocol_model               # 모든 변형, 요약표
  python -m tools.analytics_protocol_models.protocol_model V0 V3 --trace # 일부 변형, 위반 반례 출력
  python -m tools.analytics_protocol_models.protocol_model --small       # CI용 작은 시나리오
변형마다 결과가 EXPECTED와 다르면 종료 코드 1로 끝난다.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any

type State = tuple[Any, ...]

# ---------------------------------------------------------------- 변형 정의


@dataclass(frozen=True)
class Variant:
    name: str
    note: str
    # seq: 전역 시퀀스 version | counter: 행별 카운터(처리 시 행 삭제) | bool: 불리언
    marker: str = "seq"
    # eq: 선점 때 읽은 값과 같을 때만 삭제 | uncond: 무조건 삭제
    delete: str = "eq"
    # lex: (def, sv)가 사전순으로 클 때만 교체 | sv: sv가 클 때만 | none: 무조건
    guard: str = "lex"
    # plain: 잠금 없이 읽기 | hold: FOR UPDATE SKIP LOCKED를 트랜잭션 끝까지
    claim: str = "plain"
    # after: 선점 뒤 원천 읽기 | before: 원천을 먼저 읽고 선점
    snap: str = "after"
    # one: 원천을 한 스냅샷으로 | two: a, b를 READ COMMITTED 두 문장으로
    bread: str = "two"
    # same | store_then_delete | delete_then_store (따로 커밋)
    tx: str = "same"
    # 정의 변경 재처리: mark(표시 추가) | scan(옛 정의 행을 배치가 찾음) | none
    redo: str = "mark"
    # mark가 계정 advisory 잠금 안에서 표시를 쓰는가
    mark_lock: bool = True
    # excluded: 미리 뽑은 값(VALUES nextval) | inclause: 잠금 뒤 DO UPDATE에서 nextval
    upsert_ver: str = "excluded"
    # check: 저장 트랜잭션에서 현재 정의와 비교해 다르면 중단 | code: 시작 때 정의 그대로
    bdef: str = "check"
    # join: 표시+저장 행 한 문장 | stats_then_marker | marker_then_stats
    query: str = "join"
    # 조회 때 계산의 원천 읽기: one | two
    qread: str = "one"


@dataclass(frozen=True)
class Scenario:
    def_change: bool
    # stored: 저장 행 있음·표시 없음 | marker: 저장 행 없음·표시 있음(첫 저장)
    init: str
    # 원천 변경 1·2가 바꾸는 부분, 예 ('a','b') = 구간 재구성 후 라벨 확정
    parts: tuple[str, ...]

    def label(self) -> str:
        return (
            f"정의변경{'1' if self.def_change else '0'}·초기 {self.init}·변경 "
            f"{'→'.join(self.parts)}"
        )


SCENARIOS = [
    Scenario(d, i, p)
    for d in (False, True)
    for i in ("stored", "marker")
    for p in (("a", "b"), ("b", "a"))
]

# ---------------------------------------------------------------- 상태
# 전역 값의 순서: a, b, seq, marker, mlock, stats, cur_def, adv
#   marker: None | int(seq version 또는 카운터) | True(bool)
#   mlock: 표시 행을 FOR UPDATE로 쥔 배치 번호 | None
#   stats: None | 튜플 def·sv·val, val = 튜플 계산에 쓴 정의·a·b
#   adv: 계정 advisory 잠금을 쥔 쪽 'S' | 'D' | None
# 프로세스의 순서: src, dfn, b1, b2, q
A, B_, SEQ, MARK, MLOCK, STATS, DEF, ADV = range(8)
NG = 8
SOLO_RUNS = 2


def f(d: int, a: int, *, b: int) -> tuple[int, int, int]:
    """일간 통계 계산 = 정의와 원천의 함수. 값 자체로 어떤 입력에서 왔는지 드러난다."""
    return (d, a, b)


def initial(v: Variant, sc: Scenario) -> State:
    if sc.init == "stored":
        stats, marker = (1, 1, f(1, 0, b=0)), None
    else:
        stats, marker = None, {"seq": 1, "counter": 1, "bool": True}[v.marker]
    g = (0, 0, 2, marker, None, stats, 1, None)
    src = ("draw", 0, None)  # pc, k, 뽑은 version
    dfn = ("bump", None) if sc.def_change else ("done", None)
    batch = ("start", None, None, None, None, None)  # pc, mydef, mode, v, ra, rb
    # pc, qdef, m, st, ra, rb, res, valid
    q = ("idle", None, None, None, None, None, None, frozenset())
    return g + (src, dfn, batch, batch, q)


def copy_globals(s: State) -> list[Any]:
    return list(s[:NG])


def atomic_query(g: State) -> Any:
    """한 스냅샷 조회 결과(J 검사용)."""
    a, b, marker, stats, cur = g[A], g[B_], g[MARK], g[STATS], g[DEF]
    if marker is not None or stats is None or stats[0] != cur:
        return f(cur, a, b=b)
    return stats[2]


# ---------------------------------------------------------------- 저장 트랜잭션 조각


def store_ok(v: Variant, stats: Any, *, mydef: int, sv: int) -> bool:
    if stats is None or v.guard == "none":
        return True
    if v.guard == "sv":
        return sv > stats[1]
    return (mydef, sv) > (stats[0], stats[1])


def do_store(v: Variant, g: list[Any], *, mydef: int, sv: int, val: Any) -> None:
    if store_ok(v, g[STATS], mydef=mydef, sv=sv):
        g[STATS] = (mydef, sv, val)


def do_delete(v: Variant, g: list[Any], *, claimed: Any) -> None:
    if g[MARK] is None:
        return
    if v.delete == "uncond" or v.marker == "bool" or g[MARK] == claimed:
        g[MARK] = None


def marker_after_write(v: Variant, g: list[Any], *, drawn: Any) -> Any:
    """원천 handler 또는 mark가 표시를 UPSERT한 뒤의 표시 값."""
    m = g[MARK]
    if v.marker == "bool":
        return True
    if v.marker == "counter":
        return (m or 0) + 1
    if v.upsert_ver == "inclause":
        # UPSERT 한 문장 안에서 version을 뽑는다: DO UPDATE 절의 nextval은 행 잠금을 얻은 뒤
        # 평가되고(PG9), INSERT 경로는 그 문장이 넣은 행을 커밋까지 잠가 다른 표시 쓰기를 기다리게
        # 한다(PG7과 같은 대기). 그래서 같은 표시 행의 version은 커밋 순서대로 커진다. 이 쓰기는
        # 커밋 시점의 원자 단계로 둔다.
        val = g[SEQ]
        g[SEQ] += 1
        return val
    return drawn


def draw(g: list[Any]) -> int:
    val = g[SEQ]
    g[SEQ] += 1
    return val


# ---------------------------------------------------------------- 전이


def successors(v: Variant, sc: Scenario, *, s: State) -> list[Any]:
    """(이름, 다음 상태, 위반 목록) 목록. 위반은 전이 자체에서 생기는 것(I1(Q), I3, I5)."""
    out = []
    src, dfn, b1, b2, q = s[NG:]

    def emit(label: str, g: list[Any], *, procs: list[Any], viol: Any = ()) -> None:
        gt = tuple(g)
        ns = gt + tuple(procs)
        # 조회가 진행 중이면 이 상태의 정답을 유효 집합에 더한다.
        qq = ns[12]
        if qq[0] not in ("idle", "done"):
            valid = qq[7] | {f(qq[1], gt[A], b=gt[B_])}
            ns = ns[:12] + (qq[:7] + (valid,),)
        found: list[str] = list(viol)
        if (
            s[STATS] is not None
            and gt[STATS] is not None
            and gt[STATS][0] < s[STATS][0]
        ):
            found.append("I5")
        out.append((label, ns, found))

    procs = [src, dfn, b1, b2, q]

    # 원천 변경(직렬, advisory 잠금)
    pc, k, drawn = src
    if pc == "draw" and s[ADV] is None:
        g = copy_globals(s)
        val = draw(g)
        g[ADV] = "S"
        p = list(procs)
        p[0] = ("commit", k, val)
        emit(f"원천{k + 1} 시작: advisory 잠금, nextval={val}", g, procs=p)
    elif pc == "commit":
        if s[MLOCK] is not None:
            pass  # 표시 행이 배치 잠금에 막힘(I4, check_state에서 판정). 이 단계는 기다린다.
        else:
            g = copy_globals(s)
            part = sc.parts[k]
            g[A if part == "a" else B_] += 1
            g[MARK] = marker_after_write(v, g, drawn=drawn)
            g[ADV] = None
            p = list(procs)
            p[0] = (
                ("draw", k + 1, None)
                if k + 1 < len(sc.parts)
                else ("done", k + 1, None)
            )
            emit(
                f"원천{k + 1} 커밋: {'구간 a' if part == 'a' else '라벨 b'} +1, "
                f"표시={g[MARK]}",
                g,
                procs=p,
            )

    # 정의 변경
    dpc, ddrawn = dfn
    if dpc == "bump":
        g = copy_globals(s)
        g[DEF] = 2
        p = list(procs)
        p[1] = ("draw", None) if v.redo == "mark" else ("done", None)
        emit("정의 version 1→2", g, procs=p)
    elif dpc == "draw" and (not v.mark_lock or s[ADV] is None):
        g = copy_globals(s)
        val = draw(g)
        if v.mark_lock:
            g[ADV] = "D"
        p = list(procs)
        p[1] = ("commit", val)
        emit(f"정의 재처리 표시 시작: nextval={val}", g, procs=p)
    elif dpc == "commit" and s[MLOCK] is None:
        g = copy_globals(s)
        g[MARK] = marker_after_write(v, g, drawn=ddrawn)
        if v.mark_lock:
            g[ADV] = None
        p = list(procs)
        p[1] = ("done", None)
        emit(f"정의 재처리 표시 커밋: 표시={g[MARK]}", g, procs=p)

    # 배치
    for idx in (2, 3):
        bi = idx - 1
        bpc, mydef, mode, cv, ra, rb = procs[idx]
        name = f"배치{bi}"

        def put(
            newlocal: Any,
            g: list[Any],
            *,
            label: str,
            viol: Any = (),
            slot: tuple[int, str] = (idx, name),
        ) -> None:
            p = list(procs)
            p[slot[0]] = newlocal
            emit(f"{slot[1]} {label}", g, procs=p, viol=viol)

        if bpc == "start":
            nxt = "ra" if v.snap == "before" else "claim"
            put(
                (nxt, s[DEF], None, None, None, None),
                copy_globals(s),
                label=f"시작: 코드 정의={s[DEF]}",
            )
        elif bpc == "claim":
            g = copy_globals(s)
            after = "final" if v.snap == "before" else "ra"
            if s[MARK] is not None and not (v.claim == "hold" and s[MLOCK] is not None):
                if v.claim == "hold":
                    g[MLOCK] = bi
                put(
                    (after, mydef, "mark", s[MARK], ra, rb),
                    g,
                    label=f"선점: 표시={s[MARK]}"
                    f"{' (FOR UPDATE 유지)' if v.claim == 'hold' else ''}",
                )
            elif v.redo == "scan" and s[STATS] is not None and s[STATS][0] < mydef:
                put(
                    (after, mydef, "scan", s[STATS][1], ra, rb),
                    g,
                    label=f"선점: 옛 정의 저장 행 sv={s[STATS][1]}",
                )
            else:
                put(
                    ("done", mydef, None, None, None, None),
                    g,
                    label="선점: 일 없음"
                    + (" (SKIP LOCKED)" if s[MARK] is not None else ""),
                )
        elif bpc == "ra":
            if v.bread == "one":
                nxt = "claim" if v.snap == "before" else "final"
                put(
                    (nxt, mydef, mode, cv, s[A], s[B_]),
                    copy_globals(s),
                    label=f"원천 읽기(한 스냅샷): a={s[A]}, b={s[B_]}",
                )
            else:
                put(
                    ("rb", mydef, mode, cv, s[A], None),
                    copy_globals(s),
                    label=f"원천 읽기 1: a={s[A]}",
                )
        elif bpc == "rb":
            nxt = "claim" if v.snap == "before" else "final"
            put(
                (nxt, mydef, mode, cv, ra, s[B_]),
                copy_globals(s),
                label=f"원천 읽기 2: b={s[B_]}",
            )
        elif bpc in ("final", "final2"):
            val = f(mydef, ra, b=rb)
            sv = (
                cv
                if (mode == "mark" and v.marker == "seq")
                else (cv if mode == "scan" else 0)
            )
            # 중단(배치 실패)
            g = copy_globals(s)
            if s[MLOCK] == bi:
                g[MLOCK] = None
            put(("done", mydef, None, None, None, None), g, label="실패: 롤백")
            if v.bdef == "check" and s[DEF] != mydef:
                continue  # 저장 트랜잭션이 정의 불일치로 중단되는 경우는 위 롤백과 같다.
            g = copy_globals(s)
            if v.tx == "same" or mode == "scan":
                do_store(v, g, mydef=mydef, sv=sv, val=val)
                if mode == "mark":
                    do_delete(v, g, claimed=cv)
                if s[MLOCK] == bi:
                    g[MLOCK] = None
                put(
                    ("done", mydef, None, None, None, None),
                    g,
                    label=f"저장+표시 삭제 커밋: 계산값={val}, sv={sv} → "
                    f"저장={g[STATS]}, 표시={g[MARK]}",
                )
            elif bpc == "final":
                first = v.tx.split("_then_")[0]
                if first == "store":
                    do_store(v, g, mydef=mydef, sv=sv, val=val)
                else:
                    do_delete(v, g, claimed=cv)
                put(
                    ("final2", mydef, mode, cv, ra, rb),
                    g,
                    label=f"{'저장' if first == 'store' else '표시 삭제'} 커밋(따로): "
                    f"저장={g[STATS]}, 표시={g[MARK]}",
                )
            else:
                second = v.tx.split("_then_")[1]
                if second == "store":
                    do_store(v, g, mydef=mydef, sv=sv, val=val)
                else:
                    do_delete(v, g, claimed=cv)
                if s[MLOCK] == bi:
                    g[MLOCK] = None
                put(
                    ("done", mydef, None, None, None, None),
                    g,
                    label=f"{'저장' if second == 'store' else '표시 삭제'} 커밋(따로): "
                    f"저장={g[STATS]}, 표시={g[MARK]}",
                )

    # 조회
    qpc, qdef, qm, qst, qa, qb, _qres, valid = q

    def qput(newlocal: Any, label: str, *, viol: Any = ()) -> None:
        p = list(procs)
        p[4] = newlocal
        emit(f"조회 {label}", copy_globals(s), procs=p, viol=viol)

    def usable(m: Any, st: Any, *, d: int) -> bool:
        return m is None and st is not None and st[0] == d

    def finish(res: Any, label: str) -> None:
        here = valid | {f(qdef, s[A], b=s[B_])}
        viol = []
        if res[0] != qdef:
            viol.append("I3")
        elif res not in here:
            viol.append("I1")
        qput(
            ("done", qdef, qm, qst, qa, qb, res, here),
            f"{label} → 결과 {res}",
            viol=viol,
        )

    start_valid = frozenset({f(s[DEF], s[A], b=s[B_])})
    if qpc == "idle":
        if v.query == "join":
            if usable(s[MARK], s[STATS], d=s[DEF]):
                p = list(procs)
                p[4] = (
                    "done",
                    s[DEF],
                    s[MARK],
                    s[STATS],
                    None,
                    None,
                    s[STATS][2],
                    start_valid,
                )
                emit(
                    f"조회 표시+저장 한 문장: 표시={s[MARK]}, 저장={s[STATS]} → "
                    f"결과 {s[STATS][2]}",
                    copy_globals(s),
                    procs=p,
                )
            else:
                p = list(procs)
                p[4] = ("ca", s[DEF], s[MARK], s[STATS], None, None, None, start_valid)
                emit(
                    f"조회 표시+저장 한 문장: 표시={s[MARK]}, 저장={s[STATS]} → "
                    "조회 때 계산",
                    copy_globals(s),
                    procs=p,
                )
        elif v.query == "stats_then_marker":
            p = list(procs)
            p[4] = ("q2", s[DEF], None, s[STATS], None, None, None, start_valid)
            emit(f"조회 저장 행 읽기: {s[STATS]}", copy_globals(s), procs=p)
        else:
            p = list(procs)
            p[4] = ("q2", s[DEF], s[MARK], None, None, None, None, start_valid)
            emit(f"조회 표시 읽기: {s[MARK]}", copy_globals(s), procs=p)
    elif qpc == "q2":
        m = s[MARK] if v.query == "stats_then_marker" else qm
        st = qst if v.query == "stats_then_marker" else s[STATS]
        what = (
            f"표시 읽기: {s[MARK]}"
            if v.query == "stats_then_marker"
            else f"저장 행 읽기: {s[STATS]}"
        )
        if usable(m, st, d=qdef):
            finish(st[2], what)
        else:
            qput(("ca", qdef, m, st, None, None, None, valid), f"{what} → 조회 때 계산")
    elif qpc == "ca":
        if v.qread == "one":
            finish(f(qdef, s[A], b=s[B_]), f"원천 읽기(한 스냅샷): a={s[A]}, b={s[B_]}")
        else:
            qput(
                ("cb", qdef, qm, qst, s[A], None, None, valid),
                f"원천 읽기 1: a={s[A]}",
            )
    elif qpc == "cb":
        finish(f(qdef, qa, b=s[B_]), f"원천 읽기 2: b={s[B_]}")
    return out


def check_state(s: State) -> list[str]:
    viol = []
    if atomic_query(s[:NG]) != f(s[DEF], s[A], b=s[B_]):
        viol.append("I1")  # J
    if s[NG][0] == "commit" and s[MLOCK] is not None:
        viol.append("I4")
    return viol


def is_terminal(s: State) -> bool:
    return all(p[0] == "done" for p in s[NG:])


def solo_batch(v: Variant, g: State) -> tuple[State, bool]:
    """변경이 멈춘 뒤 혼자 도는 배치 1회(현재 코드)."""
    gl = list(g)
    mydef = gl[DEF]
    if gl[MARK] is not None:
        cv, mode = gl[MARK], "mark"
    elif v.redo == "scan" and gl[STATS] is not None and gl[STATS][0] < mydef:
        cv, mode = gl[STATS][1], "scan"
    else:
        return tuple(gl), False
    val = f(mydef, gl[A], b=gl[B_])
    sv = cv if (mode == "scan" or v.marker == "seq") else 0
    do_store(v, gl, mydef=mydef, sv=sv, val=val)
    if mode == "mark":
        do_delete(v, gl, claimed=cv)
    return tuple(gl), True


def converged(g: State) -> bool:
    return (
        g[MARK] is None
        and g[STATS] is not None
        and g[STATS][0] == g[DEF]
        and g[STATS][2] == f(g[DEF], g[A], b=g[B_])
    )


def check_terminal(v: Variant, s: State) -> tuple[list[str], int | None]:
    g = s[:NG]
    for runs in range(SOLO_RUNS + 1):
        if converged(g):
            return [], runs
        if runs == SOLO_RUNS:
            break
        g, worked = solo_batch(v, g)
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
    complete: bool = False
    max_depth: int = 0
    solo_runs_max: int = 0
    violations: dict[str, int] = field(default_factory=dict)
    # 위반 종류 → (상태, 끝 전이 위반이면 그 전이 이름, 그 직전 상태)
    first: dict[str, tuple[Any, Any, Any]] = field(default_factory=dict)
    parents: dict[State, Any] | None = None
    seconds: float = 0.0


def explore(v: Variant, sc: Scenario, *, keep_parents: bool = False) -> Result:
    t0 = time.perf_counter()
    init = initial(v, sc)
    seen: dict[State, Any] = {init: None}
    frontier = deque([init])
    res = Result()
    depth = {init: 0}

    def hit(kind: str, state: State, *, via: Any = None, prev: Any = None) -> None:
        res.violations[kind] = res.violations.get(kind, 0) + 1
        if kind not in res.first:
            res.first[kind] = (state, via, prev)

    for kind in check_state(init):
        hit(kind, init)
    while frontier:
        s = frontier.popleft()
        succ = successors(v, sc, s=s)
        if not succ:
            if is_terminal(s):
                res.terminals += 1
                viol, runs = check_terminal(v, s)
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
                for kind in check_state(ns):
                    hit(kind, ns)
                frontier.append(ns)
    res.states = len(seen)
    res.complete = True  # 대기열이 비었다 = 도달 가능한 모든 상태와 전이를 방문했다.
    res.parents = seen if keep_parents else None

    # 완전한 실행 순서 수(시작 → 끝 상태 경로 수). 모든 전이가 어떤 프로세스의 단계를 하나
    # 진행시키므로 상태 그래프는 순환이 없다(순환이 있으면 아래에서 RuntimeError).
    # 후위 순회로 경로 수를 더한다.
    res.orders = count_orders(v, sc, init=init)
    res.seconds = time.perf_counter() - t0
    return res


def count_orders(v: Variant, sc: Scenario, *, init: State) -> int:
    memo: dict[State, int] = {}
    onstack: set[State] = set()
    stack: list[tuple[State, list[State] | None]] = [(init, None)]
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
                    msg = "상태 그래프에 순환이 있다"
                    raise RuntimeError(msg)
                if ns not in memo:
                    stack.append((ns, None))
        else:
            memo[st] = 1 if not succ else sum(memo[ns] for ns in succ)
            onstack.discard(st)
    return memo[init]


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
    lock = f" 잠금=배치{s[MLOCK]}" if s[MLOCK] is not None else ""
    return (
        f"원천(a={s[A]}, b={s[B_]}) 정의={s[DEF]} 표시={s[MARK]}{lock} 저장={s[STATS]}"
    )


# ---------------------------------------------------------------- 변형 목록

BASE = Variant(
    "V0",
    "후보: 시퀀스 version 표시, 같으면 삭제, (정의,sv) 사전순 guard, 잠금 없는 선점, 선점 뒤 "
    "READ COMMITTED 두 문장 읽기, 저장·삭제 한 트랜잭션, 정의 변경 = 잠금 안 표시 추가, "
    "저장 때 정의 확인, 조회 = 표시+저장 한 문장 + 원천 한 스냅샷",
)

VARIANTS = [
    BASE,
    replace(
        BASE,
        name="V0s",
        note="V0에서 정의 변경 재처리를 표시 추가 대신 배치가 옛 정의 행을 찾는 방식(scan)으로",
        redo="scan",
    ),
    replace(
        BASE,
        name="V0r",
        note="V0에서 배치 원천 읽기를 한 스냅샷(REPEATABLE READ)으로",
        bread="one",
    ),
    replace(
        BASE,
        name="V1",
        note="불리언 표시 + 무조건 삭제 + 무조건 저장",
        marker="bool",
        delete="uncond",
        guard="none",
    ),
    replace(
        BASE,
        name="V2",
        note="행별 카운터 표시(처리하면 행 삭제) + 같으면 삭제 + 무조건 저장",
        marker="counter",
        guard="none",
    ),
    replace(BASE, name="V3", note="V0에서 표시 무조건 삭제", delete="uncond"),
    replace(BASE, name="V4", note="V0에서 저장 guard 없음(무조건 교체)", guard="none"),
    replace(
        BASE,
        name="V5",
        note="V0에서 guard = sv가 클 때만(ADR 문구 그대로), 재처리 mark",
        guard="sv",
    ),
    replace(
        BASE,
        name="V5s",
        note="guard = sv가 클 때만, 재처리 scan",
        guard="sv",
        redo="scan",
    ),
    replace(
        BASE,
        name="V6",
        note="V0에서 선점을 FOR UPDATE SKIP LOCKED로 트랜잭션 끝까지 유지",
        claim="hold",
    ),
    replace(
        BASE,
        name="V7",
        note="V0에서 원천을 선점 전에 읽음(스냅샷이 선점보다 이름)",
        snap="before",
    ),
    replace(
        BASE,
        name="V8",
        note="V0에서 저장과 표시 삭제를 따로 커밋(저장 먼저)",
        tx="store_then_delete",
    ),
    replace(
        BASE,
        name="V9",
        note="V0에서 저장과 표시 삭제를 따로 커밋(삭제 먼저)",
        tx="delete_then_store",
    ),
    replace(
        BASE,
        name="V10",
        note="V0에서 정의 재처리 표시를 advisory 잠금 없이, 미리 뽑은 version(EXCLUDED)으로",
        mark_lock=False,
    ),
    replace(
        BASE,
        name="V11",
        note="V10에서 표시 version을 DO UPDATE 절 nextval로(잠금 뒤 평가)",
        mark_lock=False,
        upsert_ver="inclause",
    ),
    replace(
        BASE,
        name="V12",
        note="V0에서 저장 때 정의 확인 없음(시작 때 코드 정의, 배포 중 옛 배치)",
        bdef="code",
    ),
    replace(
        BASE,
        name="V12s",
        note="V0s에서 저장 때 정의 확인 없음",
        redo="scan",
        bdef="code",
    ),
    replace(
        BASE,
        name="V12b",
        note="V5(ADR guard)에서 저장 때 정의 확인 없음",
        guard="sv",
        bdef="code",
    ),
    replace(BASE, name="V13", note="V0에서 정의 변경 재처리 없음", redo="none"),
    replace(
        BASE,
        name="V14",
        note="V0에서 조회가 저장 행 → 표시를 두 문장으로",
        query="stats_then_marker",
    ),
    replace(
        BASE,
        name="V15",
        note="V0에서 조회가 표시 → 저장 행을 두 문장으로",
        query="marker_then_stats",
    ),
    replace(
        BASE,
        name="V16",
        note="V0에서 조회 때 계산의 원천 읽기를 READ COMMITTED 두 문장으로",
        qread="two",
    ),
]

# 변형별 기대 결과: 위반한 불변식 집합(빈 집합 = 위반 0).
# 옮기기 전 원본의 전체 실행 출력 protocol_model_run.txt 끝 요약에서 옮겼다.
EXPECTED: dict[str, frozenset[str]] = {
    "V0": frozenset(),
    "V0s": frozenset(),
    "V0r": frozenset(),
    "V1": frozenset({"I1", "I2"}),
    "V2": frozenset({"I1", "I2"}),
    "V3": frozenset({"I1", "I2"}),
    "V4": frozenset({"I1", "I2"}),
    "V5": frozenset(),
    "V5s": frozenset({"I2"}),
    "V6": frozenset({"I4"}),
    "V7": frozenset({"I1", "I2"}),
    "V8": frozenset(),
    "V9": frozenset({"I1", "I2"}),
    "V10": frozenset({"I1", "I2"}),
    "V11": frozenset(),
    "V12": frozenset({"I1", "I2"}),
    "V12s": frozenset({"I1", "I2"}),
    "V12b": frozenset({"I2", "I5"}),
    "V13": frozenset({"I2"}),
    "V14": frozenset({"I1"}),
    "V15": frozenset({"I1"}),
    "V16": frozenset({"I1"}),
}

# CI용 작은 시나리오: 추천 V5와 대조 V3을 정의 변경 없는 시나리오 하나로.
# 작은 시나리오의 기대 결과(전체 탐색의 EXPECTED와 따로 둔다). --small은 이 변형만 돌리고 이 값과 비교한다.
SMALL_EXPECTED: dict[str, frozenset[str]] = {
    "V5": frozenset(),
    "V3": frozenset({"I1", "I2"}),
}
SMALL_SCENARIOS = [Scenario(def_change=False, init="stored", parts=("a", "b"))]


def verdict(kinds: frozenset[str]) -> str:
    return "통과" if not kinds else "실패 " + ",".join(sorted(kinds))


def print_counterexamples(v: Variant, rows: list[tuple[Scenario, Result]]) -> None:
    # 위반 종류마다 가장 짧은 반례(BFS라 처음 찾은 것이 가장 짧다). 시나리오 중 가장 짧은 것.
    best: dict[str, Any] = {}
    for sc, r in rows:
        for kind, (st, via, prev) in r.first.items():
            # 전이 위반(I1·I3 조회 결과, I5)은 위반 직전 상태까지의 경로 + 위반 전이로 보인다.
            path = [*trace(r, prev), (via, st)] if prev is not None else trace(r, st)
            if kind not in best or len(path) < len(best[kind][1]):
                best[kind] = (sc, path, st, via)
    for kind, (sc, path, st, via) in sorted(best.items()):
        print(f"  -- {kind} 가장 짧은 반례 ({sc.label()}, {len(path)}단계)")
        print(f"     0. 초기: {show_state(initial(v, sc))}")
        for i, (label, s2) in enumerate(path, 1):
            print(f"     {i}. {label}\n        → {show_state(s2)}")
        answer = f(st[DEF], st[A], b=st[B_])
        if kind == "I2":
            g = st[:NG]
            for n in range(SOLO_RUNS):
                g2, worked = solo_batch(v, g)
                print(
                    f"     +{n + 1}. 추가 배치(혼자): {'' if worked else '일 없음'} → "
                    f"{show_state(g2 + (None,) * 5)}"
                )
                g = g2
            print(f"     정답 {answer}와 다르거나 표시가 남음")
        if kind == "I1" and via is None:
            print(
                f"     이 상태의 한 스냅샷 조회 결과 {atomic_query(st[:NG])} ≠ 정답 {answer}"
            )


def run(names: list[str], *, show_trace: bool, small: bool) -> int:
    default = list(SMALL_EXPECTED) if small else [v.name for v in VARIANTS]
    chosen = [v for v in VARIANTS if v.name in (names or default)]
    scenarios = SMALL_SCENARIOS if small else SCENARIOS
    total_states = total_orders = 0
    summary = []
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
        summary.append((v, kinds))
        print(f"\n=== {v.name}: {v.note}")
        for sc, r in rows:
            vio = (
                ", ".join(f"{k}:{n}" for k, n in sorted(r.violations.items())) or "없음"
            )
            print(
                f"  {sc.label():28s} 상태 {r.states:>7,} 전이 {r.transitions:>8,} "
                f"끝상태 {r.terminals:>6,} 실행순서 {r.orders:>14,} 깊이 {r.max_depth:>2} "
                f"완료={'예' if r.complete else '아니오'} 추가배치≤{r.solo_runs_max} "
                f"{r.seconds:5.1f}s 위반 {vio}"
            )
        print(f"  판정: {verdict(kinds)}")
        if show_trace and agg:
            print_counterexamples(v, rows)
    print("\n=== 요약")
    mismatched = []
    unchecked: list[str] = []
    for v, kinds in summary:
        expected = (SMALL_EXPECTED if small else EXPECTED).get(v.name)
        mark = ""
        if expected is None:
            mark = " (이 시나리오의 기대 결과 없음)"
            unchecked.append(v.name)
        elif kinds != expected:
            mark = f" (기대와 다름: {verdict(expected)})"
            mismatched.append(v.name)
        print(f"  {v.name:5s} {verdict(kinds):22s} {v.note}{mark}")
    print(
        f"\n모든 변형·시나리오 합계: 상태 {total_states:,}, 실행 순서 {total_orders:,}"
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
