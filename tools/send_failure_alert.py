"""
Workflow 失败告警：供 GitHub Actions 在 invest_daily 失败时调用，发送一封简短告警邮件。
"""
import asyncio
import os
import sys

# 确保项目根在 path 中，以便 import tools.email_sender
_script_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_script_dir)
sys.path.insert(0, _project_root)

from tools.email_sender import send_gmail


def main():
    if not all([
        os.environ.get("GMAIL_SENDER"),
        os.environ.get("GMAIL_APP_PASSWORD"),
        os.environ.get("GMAIL_RECIPIENT"),
    ]):
        print("Gmail 未配置，跳过失败告警邮件。")
        sys.exit(0)

    run_id = os.environ.get("GITHUB_RUN_ID")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if run_id and repo:
        link = f"https://github.com/{repo}/actions/runs/{run_id}"
        body = f"<p>投资日报 GitHub Actions workflow 运行失败，请查看仓库 Actions 日志排查。</p><p><a href=\"{link}\">查看本次运行日志</a></p>"
    else:
        body = "<p>投资日报 GitHub Actions workflow 运行失败，请查看仓库 Actions 页面的日志排查。</p>"

    subject = "【告警】投资日报运行失败"
    success = asyncio.run(send_gmail(body, subject))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
