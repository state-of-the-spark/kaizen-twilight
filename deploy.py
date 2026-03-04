"""
Kaizen Twilight — Layer 4: Deploy
Snapshots original files, applies approved changes, updates the idea ledger, and commits.
"""

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import anthropic
import yaml


def load_config(repo_root: str) -> dict:
    config_path = Path(repo_root) / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_log(repo_root: str) -> dict:
    log_path = Path(repo_root) / "improvements" / "log.yaml"
    with open(log_path) as f:
        data = yaml.safe_load(f) or {}
    if "entries" not in data:
        data["entries"] = []
    return data


def save_log(repo_root: str, log: dict):
    log_path = Path(repo_root) / "improvements" / "log.yaml"
    with open(log_path, "w") as f:
        yaml.dump(log, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def snapshot_file(repo_root: str, file_path: str, today: str) -> str | None:
    """Copy original file to snapshots directory. Returns snapshot path or None."""
    source = Path(repo_root) / file_path
    if not source.exists():
        return None

    snapshot_dir = Path(repo_root) / "improvements" / "snapshots" / today
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Flatten path for snapshot filename (e.g., .claude/skills/daily-plan/SKILL.md -> .claude-skills-daily-plan-SKILL.md.bak)
    safe_name = file_path.replace("/", "-").replace("\\", "-") + ".bak"
    snapshot_path = snapshot_dir / safe_name
    shutil.copy2(source, snapshot_path)

    return str(snapshot_path.relative_to(repo_root))


def apply_change(client: anthropic.Anthropic, repo_root: str, proposal: dict) -> dict:
    """Use Claude to apply the proposed change to the file. Returns result dict."""
    file_path = proposal.get("file", "")
    change_desc = proposal.get("change", "")
    full_path = Path(repo_root) / file_path

    if not full_path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}

    original_content = full_path.read_text(encoding="utf-8", errors="replace")

    # Ask Sonnet to generate the updated file content
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system="""You are Kaizen Twilight's deployment agent. You apply small, precise changes to files.

Rules:
- Output ONLY the complete updated file content. No explanation, no markdown fences, no preamble.
- Make the MINIMUM change necessary. Do not reformat, reorganize, or "improve" anything beyond the stated change.
- If the change cannot be applied (e.g., the referenced content doesn't exist), output the original file unchanged and add a comment: # KAIZEN: change could not be applied — [reason]
- Preserve all existing formatting, indentation, and line endings exactly.""",
        messages=[{
            "role": "user",
            "content": f"File: {file_path}\n\nChange to apply: {change_desc}\n\nCurrent file content:\n\n{original_content}"
        }]
    )

    new_content = message.content[0].text

    # Sanity check: don't write empty files
    if not new_content.strip():
        return {"success": False, "error": "Generated content was empty"}

    # Sanity check: don't write if content is identical
    if new_content.strip() == original_content.strip():
        return {"success": False, "error": "No actual change detected"}

    full_path.write_text(new_content, encoding="utf-8")

    return {
        "success": True,
        "tokens": {
            "input": message.usage.input_tokens,
            "output": message.usage.output_tokens,
        }
    }


def git_commit(repo_root: str, files: list[str], message: str):
    """Stage and commit specified files."""
    for f in files:
        subprocess.run(["git", "add", f], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo_root,
        check=True
    )


def run_deploy(repo_root: str) -> dict:
    """Deploy approved changes."""
    config = load_config(repo_root)
    log = load_log(repo_root)
    client = anthropic.Anthropic()
    today = datetime.now().strftime("%Y-%m-%d")

    # Load approved proposals
    approved_path = Path(repo_root) / "_approved.json"
    with open(approved_path) as f:
        data = json.load(f)

    approved = data.get("approved", [])
    queued = data.get("queued", [])
    rejected = data.get("rejected", [])

    print("=== Kaizen Twilight — Layer 4: Deploy ===")
    print(f"  Approved for deployment: {len(approved)}")

    deployed = []
    failed = []
    total_tokens = {"input": 0, "output": 0}
    committed_files = []

    for prop in approved:
        prop_id = prop.get("id", "unknown")
        file_path = prop.get("file", "")
        print(f"\n  Deploying [{prop_id}]: {file_path}")

        # Step 1: Snapshot
        snapshot = snapshot_file(repo_root, file_path, today)
        if snapshot:
            print(f"    Snapshot: {snapshot}")
        else:
            print(f"    Snapshot: skipped (file not found)")

        # Step 2: Apply change
        result = apply_change(client, repo_root, prop)
        if result.get("tokens"):
            total_tokens["input"] += result["tokens"]["input"]
            total_tokens["output"] += result["tokens"]["output"]

        if result["success"]:
            print(f"    Applied successfully")
            prop["status"] = "deployed"
            prop["date_resolved"] = today
            prop["snapshot"] = snapshot
            deployed.append(prop)
            committed_files.append(file_path)
        else:
            print(f"    FAILED: {result['error']}")
            prop["status"] = "proposed"  # Keep as proposed for retry
            prop["deploy_error"] = result["error"]
            failed.append(prop)

    # Step 3: Update log.yaml with all proposals
    for prop in deployed + queued + rejected + failed:
        log_entry = {
            "id": prop.get("id"),
            "date_proposed": prop.get("date", today),
            "status": prop.get("status"),
            "file": prop.get("file"),
            "summary": prop.get("finding", prop.get("change", "")),
            "effort": prop.get("effort"),
        }
        if prop.get("date_resolved"):
            log_entry["date_resolved"] = prop["date_resolved"]
        if prop.get("snapshot"):
            log_entry["snapshot"] = prop["snapshot"]
        if prop.get("queue_reason"):
            log_entry["reason"] = prop["queue_reason"]
        if prop.get("reject_reason"):
            log_entry["reason"] = prop["reject_reason"]
        if prop.get("deploy_error"):
            log_entry["reason"] = prop["deploy_error"]

        log["entries"].append(log_entry)

    save_log(repo_root, log)
    committed_files.append("improvements/log.yaml")

    # Step 4: Git commit if anything was deployed
    if deployed:
        summaries = [d.get("finding", d.get("change", "improvement"))[:60] for d in deployed]
        commit_msg = f"[kaizen] {len(deployed)} improvement(s): " + "; ".join(summaries)
        if len(commit_msg) > 120:
            commit_msg = commit_msg[:117] + "..."

        # Also add snapshots
        snapshot_dir = f"improvements/snapshots/{today}"
        committed_files.append(snapshot_dir)

        try:
            git_commit(repo_root, committed_files, commit_msg)
            print(f"\n  Committed: {commit_msg}")
        except subprocess.CalledProcessError as e:
            print(f"\n  WARNING: Git commit failed: {e}")
    else:
        # Still commit log updates if there are queued/rejected entries
        if queued or rejected:
            try:
                git_commit(repo_root, ["improvements/log.yaml"], f"[kaizen] Log update — {len(queued)} queued, {len(rejected)} rejected")
                print(f"\n  Committed log update")
            except subprocess.CalledProcessError:
                pass

    print(f"\n  Deploy complete: {len(deployed)} deployed, {len(failed)} failed")
    print(f"  Tokens used: {total_tokens['input']} in / {total_tokens['output']} out")

    return {
        "deployed": deployed,
        "queued": queued,
        "rejected": rejected,
        "failed": failed,
        "tokens": total_tokens,
    }


if __name__ == "__main__":
    repo_root = os.environ.get("REPO_ROOT", ".")
    results = run_deploy(repo_root)
    output_path = Path(repo_root) / "_deploy_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Results written to {output_path}")
