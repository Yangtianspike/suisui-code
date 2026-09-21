"""TUI task notification 测试：build_task_notification 格式。"""

import pytest
from suisuicode.task.manager import BackgroundTask, Status
from suisuicode.tui.tasks import build_task_notification


def test_build_task_notification_completed():
    bt = BackgroundTask(
        id="task_abc123",
        name="my-worker",
        status=Status.COMPLETED,
        result="all done",
    )
    notif = build_task_notification(bt)
    assert "<task-notification>" in notif
    assert "</task-notification>" in notif
    assert "task_abc123" in notif
    assert 'name="my-worker"' in notif
    assert "completed" in notif
    assert "all done" in notif


def test_build_task_notification_no_name():
    bt = BackgroundTask(
        id="task_xyz",
        name="",
        status=Status.FAILED,
        result="something broke",
    )
    notif = build_task_notification(bt)
    assert "task_xyz" in notif
    assert "failed" in notif
    assert "something broke" in notif
    # 无名时不输出 name= 属性
    assert 'name=""' not in notif


def test_build_task_notification_with_err():
    bt = BackgroundTask(
        id="task_err",
        name="bad",
        status=Status.FAILED,
        err=RuntimeError("boom"),
        result="",
    )
    notif = build_task_notification(bt)
    assert "failed" in notif
    # result 为空时应展示 err 信息
    assert "boom" in notif


def test_build_task_notification_running():
    bt = BackgroundTask(
        id="task_run",
        name="worker",
        status=Status.RUNNING,
        result="",
    )
    notif = build_task_notification(bt)
    assert "running" in notif
