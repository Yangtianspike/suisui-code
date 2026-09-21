"""T2-T4 + state 单元测试。"""

import re
import time
import threading

from suisuicode.compact.state import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
    open_session_context,
    parse_session_time,
)


def test_new_session_context():
    ctx = new_session_context(".")
    assert "-" in ctx.session_id
    assert "tool-results" in ctx.spill_dir
    assert ctx.session_dir.endswith(ctx.session_id)


def test_new_session_id_format():
    """验证 session ID 格式为 YYYYMMDD-HHMMSS-xxxx。"""
    ctx = new_session_context(".")
    sid = ctx.session_id
    pattern = r"^\d{8}-\d{6}-[0-9a-f]{4}$"
    assert re.match(pattern, sid), f"session ID {sid!r} 不匹配 {pattern}"


def test_new_session_context_unique():
    a = new_session_context(".")
    b = new_session_context(".")
    assert a.session_id != b.session_id


def test_parse_session_time():
    """从新格式 session ID 解析时间。"""
    from datetime import datetime

    sid = "20260601-143022-a1b2"
    dt = parse_session_time(sid)
    assert dt == datetime(2026, 6, 1, 14, 30, 22)


def test_open_session_context():
    """open_session_context 不创建目录，只填充字段。"""
    ctx = open_session_context("/workspace", "20260601-143022-a1b2")
    assert ctx.session_id == "20260601-143022-a1b2"
    assert "/workspace/.suisuicode/sessions/20260601-143022-a1b2" in ctx.session_dir.replace("\\", "/")
    assert ctx.spill_dir.endswith("tool-results")


def test_decide_once_kept():
    s = ContentReplacementState()
    result = s.decide_once("id1", "original", lambda: ("kept", ""))
    assert result == "original"
    assert "id1" in s._seen_ids
    assert "id1" not in s._replacements


def test_decide_once_replaced():
    s = ContentReplacementState()
    result = s.decide_once("id1", "original", lambda: ("replaced", "preview_str"))
    assert result == "preview_str"
    assert "id1" in s._seen_ids
    assert s._replacements["id1"] == "preview_str"


def test_decide_once_freeze_kept():
    s = ContentReplacementState()
    s.decide_once("id1", "original", lambda: ("kept", ""))
    # 第二次调用不再走 decide 回调，直接返回原 content
    called = []
    result = s.decide_once("id1", "original", lambda: called.append(1) or ("replaced", "x"))
    assert result == "original"
    assert called == []


def test_decide_once_freeze_replaced():
    s = ContentReplacementState()
    first = s.decide_once("id1", "original", lambda: ("replaced", "preview_v1"))
    second = s.decide_once("id1", "original", lambda: ("replaced", "preview_v2"))
    assert first == second
    assert first == "preview_v1"


def test_decide_once_skip():
    s = ContentReplacementState()
    result = s.decide_once("id1", "original", lambda: ("skip", ""))
    assert result == "original"
    assert "id1" not in s._seen_ids
    # 下一次仍走 decide
    result2 = s.decide_once("id1", "original", lambda: ("kept", ""))
    assert result2 == "original"
    assert "id1" in s._seen_ids


def test_circuit_breaker():
    cb = CompactCircuitBreaker()
    assert not cb.tripped()
    cb.record_failure()
    cb.record_failure()
    assert not cb.tripped()
    cb.record_failure()
    assert cb.tripped()
    cb.record_success()
    assert not cb.tripped()


def test_recovery_state_snapshot_order():
    rs = RecoveryState()
    rs.record_file("/a.txt", "content a")
    time.sleep(0.01)
    rs.record_file("/b.txt", "content b")
    time.sleep(0.01)
    rs.record_file("/c.txt", "content c")

    snap = rs.snapshot()
    assert len(snap) == 3
    # 按时间戳倒序：c, b, a（路径可能被 resolve，用 endswith 检查）
    assert snap[0].path.endswith("c.txt")
    assert snap[1].path.endswith("b.txt")
    assert snap[2].path.endswith("a.txt")


def test_recovery_state_snapshot_is_copy():
    rs = RecoveryState()
    rs.record_file("/a.txt", "hello")
    snap = rs.snapshot()
    snap.clear()
    assert len(rs.snapshot()) == 1


def test_recovery_state_concurrent():
    rs = RecoveryState()
    barrier = threading.Barrier(50, timeout=5)

    def writer(i):
        barrier.wait()
        for _ in range(10):
            rs.record_file(f"/file_{i}.txt", f"content_{i}")
            rs.snapshot()

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 无异常即通过


def test_auto_tracking_concurrent():
    cb = CompactCircuitBreaker()
    barrier = threading.Barrier(20, timeout=5)

    def worker():
        barrier.wait()
        for _ in range(5):
            cb.record_failure()
            cb.record_success()
            cb.tripped()

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
