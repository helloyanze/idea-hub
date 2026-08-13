"""通知模块：hermes send（QQ 推送）+ notifications 表双写。"""
import subprocess
from idea_hub import models


def send(conn, *, task_id, type, title, body, qq_target=None):
    """双写通知：notifications 表必写；qq_target 非空时调 hermes send。
    hermes send 失败只记日志（notifications 表兜底，Web 端可见），不抛异常。"""
    if qq_target is None:
        qq_target = models.get_setting(conn, "qq_target", "")
    nid = models.create_notification(conn, task_id=task_id, type=type,
                                     title=title, body=body)
    if qq_target:
        try:
            msg = f"{title}\n{body}"
            subprocess.run(["hermes", "send", "--to", qq_target, "--quiet", msg],
                           capture_output=True, text=True, timeout=30)
        except Exception as exc:  # 子进程缺失/超时/非零退出均不阻断
            print(f"[notify] hermes send failed (notification {nid} kept in DB): {exc}")
    return nid
