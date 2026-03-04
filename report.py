"""
Kaizen Twilight — Layer 5: Report
Writes a decision markdown file and sends an HTML morning summary email.
"""

import json
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import anthropic
import yaml


def load_config(repo_root: str) -> dict:
    config_path = Path(repo_root) / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def write_decision_file(repo_root: str, deploy_results: dict, total_tokens: dict) -> str:
    """Write a decisions/ markdown file with the full kaizen report."""
    today = datetime.now().strftime("%Y-%m-%d")
    deployed = deploy_results.get("deployed", [])
    queued = deploy_results.get("queued", [])
    rejected = deploy_results.get("rejected", [])
    failed = deploy_results.get("failed", [])

    lines = [
        "---",
        "type: decision",
        "status: executed",
        f"date: {today}",
        "topics: [kaizen-twilight, automation, nightly]",
        "---",
        f"# Kaizen Twilight Report — {today}",
        "",
    ]

    # Summary
    total = len(deployed) + len(queued) + len(rejected) + len(failed)
    lines.append(f"**{total} proposals** processed: {len(deployed)} deployed, {len(queued)} queued, {len(rejected)} rejected, {len(failed)} failed.")
    lines.append("")

    # Deployed
    if deployed:
        lines.append("## Deployed")
        for d in deployed:
            lines.append(f"- **[{d.get('id')}]** `{d.get('file')}` — {d.get('finding', '')}")
            if d.get("change"):
                lines.append(f"  - **Fix:** {d['change']}")
            if d.get("snapshot"):
                lines.append(f"  - Snapshot: `{d['snapshot']}`")
            lines.append(f"  - Effort: {d.get('effort')} | Category: {d.get('category', 'n/a')}")
        lines.append("")

    # Queued for review
    if queued:
        lines.append("## Queued for Review")
        for q in queued:
            lines.append(f"- **[{q.get('id')}]** `{q.get('file')}` — {q.get('finding', q.get('change', ''))}")
            lines.append(f"  - Reason: {q.get('queue_reason', 'n/a')}")
            lines.append(f"  - Effort: {q.get('effort')} | Impact: {q.get('impact', 'n/a')}")
        lines.append("")

    # Rejected/Failed
    if rejected or failed:
        lines.append("## Rejected / Failed")
        for r in rejected + failed:
            lines.append(f"- **[{r.get('id')}]** `{r.get('file')}` — {r.get('reject_reason', r.get('deploy_error', 'unknown'))}")
        lines.append("")

    # Token usage
    lines.append("## Token Usage")
    lines.append(f"- Total input: {total_tokens.get('input', 0)}")
    lines.append(f"- Total output: {total_tokens.get('output', 0)}")
    lines.append("")

    content = "\n".join(lines)
    decisions_dir = Path(repo_root) / "decisions"
    decisions_dir.mkdir(exist_ok=True)
    file_path = decisions_dir / f"{today}-kaizen-report.md"
    file_path.write_text(content, encoding="utf-8")

    return str(file_path.relative_to(repo_root))


def build_html_email(deploy_results: dict) -> str:
    """Build a styled HTML email body."""
    today = datetime.now().strftime("%A, %B %d, %Y")
    deployed = deploy_results.get("deployed", [])
    queued = deploy_results.get("queued", [])

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">

<h1 style="color: #1a1a2e; border-bottom: 3px solid #e94560; padding-bottom: 10px;">
  Kaizen Twilight
</h1>
<p style="color: #666; margin-top: -5px;">{today}</p>
"""

    if deployed:
        html += '<h2 style="color: #16213e;">Deployed Overnight</h2>\n'
        for d in deployed:
            fix_line = f"<br><strong>Fix:</strong> {d['change']}" if d.get('change') else ""
            html += f"""<div style="background: #f0f8f0; border-left: 4px solid #4caf50; padding: 12px; margin-bottom: 10px; border-radius: 4px;">
  <strong>{d.get('id')}</strong><br>
  <code style="background: #e8e8e8; padding: 2px 6px; border-radius: 3px;">{d.get('file')}</code><br>
  {d.get('finding', '')}
  {fix_line}
  <br><span style="color: #888; font-size: 0.9em;">Effort: {d.get('effort')} | Category: {d.get('category', 'n/a')}</span>
</div>\n"""
    else:
        html += '<p style="color: #888;">No changes deployed tonight. All systems looked healthy.</p>\n'

    if queued:
        html += '<h2 style="color: #16213e;">Queued for Your Review</h2>\n'
        for q in queued:
            html += f"""<div style="background: #fff8e1; border-left: 4px solid #ff9800; padding: 12px; margin-bottom: 10px; border-radius: 4px;">
  <strong>{q.get('id')}</strong><br>
  <code style="background: #e8e8e8; padding: 2px 6px; border-radius: 3px;">{q.get('file')}</code><br>
  {q.get('finding', q.get('change', ''))}
  <br><span style="color: #888; font-size: 0.9em;">Reason queued: {q.get('queue_reason', 'n/a')}</span>
</div>\n"""

    html += """
<hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
<p style="color: #aaa; font-size: 0.85em;">
  Kaizen Twilight — continuous improvement while you sleep.<br>
  Review queued items in improvements/log.yaml or open a Claude Code session.
</p>
</body></html>"""

    return html


def send_email(config: dict, deploy_results: dict):
    """Send the morning summary email via Gmail SMTP."""
    email_config = config.get("email", {})
    if not email_config.get("enabled", False):
        print("  Email disabled in config. Skipping.")
        return

    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not app_password:
        print("  WARNING: GMAIL_APP_PASSWORD not set. Skipping email.")
        return

    email_from = email_config.get("from", "")
    email_to = email_config.get("to", [])
    if not email_from or not email_to:
        print("  WARNING: Email from/to not configured. Skipping.")
        return

    deployed = deploy_results.get("deployed", [])
    queued = deploy_results.get("queued", [])
    total_changes = len(deployed) + len(queued)

    subject_template = email_config.get("subject_template", "Kaizen Twilight — {count} improvements overnight")
    subject = subject_template.format(count=total_changes)

    html_body = build_html_email(deploy_results)

    # Plain text fallback
    plain = f"Kaizen Twilight Report\n\nDeployed: {len(deployed)}\nQueued: {len(queued)}\n\nCheck decisions/ folder for full details."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = ", ".join(email_to)
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(email_from, app_password)
            server.sendmail(email_from, email_to, msg.as_string())
        print(f"  Email sent to: {', '.join(email_to)}")
    except Exception as e:
        print(f"  ERROR sending email: {e}")


def run_report(repo_root: str) -> dict:
    """Generate the morning report."""
    config = load_config(repo_root)

    # Load deploy results
    deploy_path = Path(repo_root) / "_deploy_results.json"
    with open(deploy_path) as f:
        deploy_results = json.load(f)

    # Aggregate token usage from all layers
    total_tokens = {"input": 0, "output": 0}
    for temp_file in ["_survey_results.json", "_proposals.json", "_deploy_results.json"]:
        temp_path = Path(repo_root) / temp_file
        if temp_path.exists():
            with open(temp_path) as f:
                data = json.load(f)
            tokens = data.get("tokens", {})
            total_tokens["input"] += tokens.get("input", 0)
            total_tokens["output"] += tokens.get("output", 0)

    print("=== Kaizen Twilight — Layer 5: Report ===")

    # Write decision file
    decision_path = write_decision_file(repo_root, deploy_results, total_tokens)
    print(f"  Decision file: {decision_path}")

    # Send email
    send_email(config, deploy_results)

    # Clean up temp files
    for temp_file in ["_survey_results.json", "_proposals.json", "_approved.json", "_deploy_results.json"]:
        temp_path = Path(repo_root) / temp_file
        if temp_path.exists():
            temp_path.unlink()
            print(f"  Cleaned up: {temp_file}")

    print("\n  Report complete.")

    return {
        "decision_file": decision_path,
        "total_tokens": total_tokens,
    }


if __name__ == "__main__":
    repo_root = os.environ.get("REPO_ROOT", ".")
    run_report(repo_root)
