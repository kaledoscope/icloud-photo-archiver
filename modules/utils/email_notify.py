#!/usr/bin/env python3.11
"""
email_notify.py — 通过 SMTP 发送带附件的备份报告邮件

SMTP 凭据来自 config/secrets.env，开关是 config/config.env 的 EMAIL_ENABLED。

用法（从项目根调用）::

    python -m modules.utils.email_notify <主题> <正文>
    python -m modules.utils.email_notify <主题> <正文> --attach <报告文件路径>
    python -m modules.utils.email_notify <主题> --body-file <正文文件> --attach <附件>
"""

import os
import sys
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

# ===== 配置（统一来自 config/，SMTP 凭据在 config/secrets.env）=====
from modules.utils.config import get, get_bool, get_int


def send_email(subject: str, body: str, attach_path: str = "") -> bool:
    """发送邮件（可选附件），返回是否成功"""
    # 总开关。凭据齐全不代表"该发"——生产里关掉 EMAIL_ENABLED 就必须真的一封都不发，
    # 否则编排脚本日志写着"未启用"，邮件却照发，说一套做一套。
    if not get_bool("EMAIL_ENABLED", False):
        print("邮件通知未启用（config/config.env 的 EMAIL_ENABLED=false），跳过发送")
        return False

    host = get("EMAIL_SMTP_HOST", "")
    port = get_int("EMAIL_SMTP_PORT", 587)
    user = get("EMAIL_FROM", "")
    password = get("EMAIL_PASSWORD", "")
    to = get("EMAIL_TO", "")

    if not all([host, user, password, to]):
        print("⚠️ 邮件配置不完整，跳过发送")
        return False

    # 拼接正文
    full_body = body + f"\n\n---\nODC v1.0 · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    # 构建邮件
    if attach_path and os.path.isfile(attach_path):
        msg = MIMEMultipart()
        msg.attach(MIMEText(full_body, "plain", "utf-8"))
        # 附件
        with open(attach_path, "rb") as fh:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(fh.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{os.path.basename(attach_path)}"',
        )
        msg.attach(part)
    else:
        msg = MIMEText(full_body, "plain", "utf-8")

    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to

    try:
        server = smtplib.SMTP(host, port, timeout=30)
        server.starttls()
        server.login(user, password)
        server.sendmail(user, [to], msg.as_string())
        server.quit()
        print(f"✅ 邮件已发送至 {to}")
        if attach_path:
            print(f"   附件: {os.path.basename(attach_path)}")
        return True
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")
        return False


def main():
    args = sys.argv[1:]
    subject = ""
    body = ""
    attach_path = ""
    body_file = ""

    i = 0
    while i < len(args):
        if args[i] == "--attach":
            i += 1
            attach_path = args[i]
        elif args[i] == "--body-file":
            i += 1
            body_file = args[i]
        elif not subject:
            subject = args[i]
        elif not body:
            body = args[i]
        i += 1

    if body_file and os.path.isfile(body_file):
        with open(body_file, "r") as f:
            body = f.read()

    if not subject or not body:
        print("用法: email_notify.py <主题> <正文> [--attach <文件>] [--body-file <正文文件>]")
        sys.exit(1)

    success = send_email(subject, body, attach_path)
    # 开关关闭时"没发出去"是预期行为，不是错误 —— 别让手动调用看到误导性的退出码
    if not get_bool("EMAIL_ENABLED", False):
        sys.exit(0)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
