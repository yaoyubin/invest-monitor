"""
Gmail 发送：供 UGC 监控与投资日报共用
"""
import asyncio
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


async def send_gmail(html_content, subject):
    """
    使用 Gmail SMTP 发送 HTML 邮件。
    html_content: 邮件正文 HTML 字符串
    subject: 邮件主题
    返回: True 成功, False 失败
    """
    sender_email = os.environ.get("GMAIL_SENDER")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient_email = os.environ.get("GMAIL_RECIPIENT")

    if not sender_email or not app_password or not recipient_email:
        print("❌ Gmail配置未设置（需要：GMAIL_SENDER, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT）")
        return False

    full_html = f"""
    <html>
    <head>
        <meta charset="utf-8">
    </head>
    <body>
        {html_content}
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg.attach(MIMEText(full_html, "html", "utf-8"))

    def _send_sync():
        try:
            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(sender_email, app_password)
                server.send_message(msg)
            return True, None
        except Exception as e:
            return False, str(e)

    try:
        success, error_msg = await asyncio.to_thread(_send_sync)
        if success:
            print("✅ 邮件发送成功")
            return True
        print(f"❌ 邮件发送失败: {error_msg}")
        return False
    except Exception as e:
        print(f"❌ 邮件发送异常: {e}")
        return False
