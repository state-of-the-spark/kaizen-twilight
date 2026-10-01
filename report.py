"""
Kaizen Twilight — Layer 5: Report
Writes a decision markdown file and sends a plain-text morning summary email.

Format: follow reference/systems/readable-email-standard.md (headings + bullets,
25 words max per bullet, send body_text only).
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
    config_path = Path(repo_root) / "agents" / "kaizen-twilight" / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def write_decision_file(repo_root: str, deploy_results: dict, total_tokens: dict) -> str:
    """Write a decisions/ markdown file with the full kaizen report."""
    today = datetime.now().strftime("%Y-%m-%d")
    deployed = deploy_results.get("deployed", [])
    queued = deploy_results.get("queued", [])
    rejected = deploy_results.get("rejected", [])
    failed = deploy_results.get("failed", [])

    total = len(deployed) + len(queued) + len(rejected) + len(failed)
    summary = f"{len(deployed)} deployed, {len(queued)} queued, {len(rejected) + len(failed)} rejected/failed ({total} proposals)"

    lines = [
        "---",
        "type: decision",
        "status: executed",
        f"date: {today}",
        "moc: operations",
        f'summary: "{summary}"',
        "topics: [kaizen-twilight, automation, nightly]",
        "---",
        f"# Kaizen Twilight Report — {today}",
        "",
    ]

    # Summary
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

    # Related — born connected: anchor every report to the operations MOC hub
    lines.append("## Related")
    lines.append("- [[operations]] — nightly kaizen ops log, anchored here")
    lines.append("")

    content = "\n".join(lines)
    decisions_dir = Path(repo_root) / "decisions"
    decisions_dir.mkdir(exist_ok=True)
    file_path = decisions_dir / f"{today}-kaizen-report.md"
    file_path.write_text(content, encoding="utf-8")

    return str(file_path.relative_to(repo_root))


def _words(text: str, limit: int) -> str:
    """Truncate to `limit` words, appending an ellipsis if cut."""
    if not text:
        return ""
    words = str(text).split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit]) + "..."


def build_digest_text(deploy_results: dict) -> str:
    """Build the plain-text digest body: DEPLOYED / QUEUED FOR YOU as one-line
    bullets, rationale as a max-20-word sub-bullet. No headings deeper than
    two levels, no paragraphs."""
    today = datetime.now().strftime("%A, %B %d, %Y")
    deployed = deploy_results.get("deployed", [])
    queued = deploy_results.get("queued", [])

    lines = [f"KAIZEN TWILIGHT - {today}", ""]

    lines.append("DEPLOYED")
    if deployed:
        for d in deployed:
            file_part = f" ({d.get('file')})" if d.get("file") else ""
            lines.append(f"- {d.get('id')}{file_part}: {_words(d.get('finding', ''), 25)}")
            if d.get("change"):
                lines.append(f"  - Fix: {_words(d['change'], 20)}")
    else:
        lines.append("- Nothing deployed tonight, all systems healthy")
    lines.append("")

    lines.append("QUEUED FOR YOU")
    if queued:
        for q in queued:
            file_part = f" ({q.get('file')})" if q.get("file") else ""
            summary = q.get("finding", q.get("change", ""))
            lines.append(f"- {q.get('id')}{file_part}: {_words(summary, 25)}")
            if q.get("queue_reason"):
                lines.append(f"  - Why queued: {_words(q['queue_reason'], 20)}")
    else:
        lines.append("- Nothing queued")
    lines.append("")

    lines.append('Review queued items by saying "kaizen review" in your next session.')

    return "\n".join(lines)


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

    email_from = email_config.get("from", "grant@stateofthespark.com")
    email_to = email_config.get("to", [])
    if not email_to:
        print("  WARNING: No email_to addresses configured. Skipping.")
        return

    deployed = deploy_results.get("deployed", [])
    queued = deploy_results.get("queued", [])
    total_changes = len(deployed) + len(queued)

    subject_template = email_config.get("subject_template", "Kaizen Twilight — {count} improvements overnight")
    subject = subject_template.format(count=total_changes)

    # body_text only per reference/systems/readable-email-standard.md - no HTML body.
    body_text = build_digest_text(deploy_results)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = ", ".join(email_to)
    msg.attach(MIMEText(body_text, "plain"))

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
    deploy_path = Path(repo_root) / "agents" / "kaizen-twilight" / "_deploy_results.json"
    with open(deploy_path) as f:
        deploy_results = json.load(f)

    # Aggregate token usage from all layers
    total_tokens = {"input": 0, "output": 0}
    for temp_file in ["_survey_results.json", "_proposals.json", "_deploy_results.json"]:
        temp_path = Path(repo_root) / "agents" / "kaizen-twilight" / temp_file
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
        temp_path = Path(repo_root) / "agents" / "kaizen-twilight" / temp_file
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
