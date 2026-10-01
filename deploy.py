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
    config_path = Path(repo_root) / "agents" / "kaizen-twilight" / "config.yaml"
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


def _extract_json(text: str) -> str:
    """Return the outermost {...} JSON substring from a model response, tolerating
    markdown fences or stray prose around it."""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return text.strip()


def apply_change(client: anthropic.Anthropic, repo_root: str, proposal: dict) -> dict:
    """Apply the proposed change as a set of exact search/replace edits.

    We do NOT regenerate the whole file. Regenerating whole files truncated large
    files whenever the model hit its output ceiling (the 2026-06-09 bug). Instead
    we ask the model for a small list of {old_string, new_string} edits and apply
    them deterministically in Python: each old_string must match exactly once, and
    the change is all-or-nothing. Nothing is ever written from a truncated or
    partial model response. This is also far cheaper than round-tripping the file.
    """
    file_path = proposal.get("file", "")
    change_desc = proposal.get("change", "")
    full_path = Path(repo_root) / file_path

    if not full_path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}

    original_content = full_path.read_text(encoding="utf-8", errors="replace")

    # Ask the model for a minimal set of search/replace edits (NOT the whole file).
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        system="""You are Kaizen Twilight's deployment agent. You apply ONE small, precise change to a file by emitting exact search/replace edits.

Output ONLY a JSON object, with no markdown fences and no prose, in exactly this shape:
{"edits": [{"old_string": "<verbatim text to find>", "new_string": "<replacement text>"}]}

Rules:
- Each old_string MUST be copied verbatim from the current file, including exact whitespace and indentation, and MUST be long enough to occur EXACTLY ONCE in the file. Add surrounding context lines if needed for uniqueness.
- Make the MINIMUM change necessary. Do not reformat, reorganize, or improve anything beyond the stated change. Keep each edit small and targeted.
- To INSERT new content, set old_string to a unique nearby anchor and set new_string to that same anchor (unchanged) plus the inserted content.
- To DELETE, set new_string to "" or to the anchor with the unwanted part removed.
- If the change cannot be applied (referenced content does not exist, or it is already done), return {"edits": []} and nothing else.
- Never output the whole file. Only the edits.""",
        messages=[{
            "role": "user",
            "content": f"File: {file_path}\n\nChange to apply: {change_desc}\n\nCurrent file content:\n\n{original_content}"
        }]
    )

    tokens = {"input": message.usage.input_tokens, "output": message.usage.output_tokens}

    # Guard 1: truncated output -> the edits JSON is incomplete. Refuse (fail-safe).
    if message.stop_reason == "max_tokens":
        return {"success": False, "error": "Model output hit max_tokens; edits incomplete, refused.", "tokens": tokens}

    raw = message.content[0].text if message.content else ""
    try:
        parsed = json.loads(_extract_json(raw))
        edits = parsed.get("edits", [])
    except (json.JSONDecodeError, AttributeError) as e:
        return {"success": False, "error": f"Could not parse edits JSON: {e}", "tokens": tokens}

    if not isinstance(edits, list) or not edits:
        return {"success": False, "error": "No applicable edits returned", "tokens": tokens}

    # Apply edits all-or-nothing on a working copy. Each old_string must match
    # exactly once: 0 = not found, >1 = ambiguous. Either aborts the whole change
    # so we never write a partially-applied edit.
    working = original_content
    for i, edit in enumerate(edits):
        old = edit.get("old_string")
        new = edit.get("new_string", "")
        if old is None:
            return {"success": False, "error": f"Edit {i} missing old_string", "tokens": tokens}
        count = working.count(old)
        if count == 0:
            return {"success": False, "error": f"Edit {i}: old_string not found (no exact match)", "tokens": tokens}
        if count > 1:
            return {"success": False, "error": f"Edit {i}: old_string matched {count} times (ambiguous)", "tokens": tokens}
        working = working.replace(old, new, 1)

    # Guard 2: result must actually differ from the original.
    if working.strip() == original_content.strip():
        return {"success": False, "error": "No actual change detected", "tokens": tokens}

    # Guard 3: result must not be empty.
    if not working.strip():
        return {"success": False, "error": "Result was empty", "tokens": tokens}

    # Guard 4 (belt + suspenders): a genuine 1% improvement never shrinks a file by
    # more than 25%. If it did, the edits are almost certainly botched -- refuse.
    if len(working) < 0.75 * len(original_content):
        return {
            "success": False,
            "error": (f"Result shrank from {len(original_content)} to {len(working)} "
                      f"chars (>25%); refused as likely botched edit."),
            "tokens": tokens,
        }

    full_path.write_text(working, encoding="utf-8")

    return {"success": True, "tokens": tokens, "edits_applied": len(edits)}


def write_redline_draft(repo_root: str, proposal: dict, today: str) -> str:
    """Convert a redlined proposal (e.g. one targeting goals.md) into a decision
    DRAFT for Grant to sign. The redlined file is never touched. Returns the
    draft's repo-relative path. (Decision C, 2026-05-27.)"""
    target = proposal.get("file", "reference/systems/goals.md")
    finding = proposal.get("finding", "")
    change = proposal.get("change", "")
    prop_id = proposal.get("id", "unknown")

    drafts_dir = Path(repo_root) / "decisions"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    draft_path = drafts_dir / f"{today}-linchpin-revision-DRAFT.md"

    body = f"""---
type: decision
status: draft
date: {today}
topics: [goals, linchpin, kaizen-flagged]
source: kaizen-twilight
proposal_id: {prop_id}
---

# Linchpin Revision — DRAFT (kaizen-flagged, awaiting Grant's signature)

The nightly kaizen agent observed something about the master goal and is NOT
allowed to change `{target}` itself. It has surfaced the observation here for
Grant to decide on. **`goals.md` has not been changed.**

## What the agent observed

{finding or "(no finding text provided)"}

## What it would have changed (do not auto-apply)

{change or "(no change text provided)"}

## Grant fills this in

- **Is a revision warranted?** (yes / no):
- **New deadline (if any):**
- **Revision reason (in Grant's words):**
- **Signed by:**
- **Date signed:**

## To accept

1. Fill in the section above.
2. Change `status: draft` -> `status: accepted`.
3. Apply the corresponding edit to `reference/systems/goals.md` yourself
   (or ask Lumen to apply it). Until then, the goal stands as written and the
   slippage stays visible.

_Rationale: decisions/2026-05-27-northstar-goal-reinforcement-system.md (Decision C)._
"""
    # Do not clobber an existing unsigned draft from the same night.
    if draft_path.exists():
        existing = draft_path.read_text(encoding="utf-8", errors="replace")
        draft_path.write_text(existing + "\n\n---\n\n" + body, encoding="utf-8")
    else:
        draft_path.write_text(body, encoding="utf-8")

    return str(draft_path.relative_to(repo_root))


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
    approved_path = Path(repo_root) / "agents" / "kaizen-twilight" / "_approved.json"
    with open(approved_path) as f:
        data = json.load(f)

    approved = data.get("approved", [])
    queued = data.get("queued", [])
    rejected = data.get("rejected", [])
    redline_drafts = data.get("redline_drafts", [])

    print("=== Kaizen Twilight — Layer 4: Deploy ===")
    print(f"  Approved for deployment: {len(approved)}")
    print(f"  Redline drafts to write: {len(redline_drafts)}")

    deployed = []
    failed = []
    total_tokens = {"input": 0, "output": 0}
    committed_files = []

    # Redline drafts: write a decision draft instead of editing the redlined
    # file. Never touches the redlined file (e.g. goals.md). (Decision C.)
    for prop in redline_drafts:
        prop_id = prop.get("id", "unknown")
        try:
            draft_rel = write_redline_draft(repo_root, prop, today)
            prop["status"] = "redline_draft_written"
            prop["draft_file"] = draft_rel
            committed_files.append(draft_rel)
            print(f"  [{prop_id}] REDLINE DRAFT -> {draft_rel} (goals.md untouched)")
        except Exception as e:  # noqa: BLE001
            print(f"  [{prop_id}] REDLINE DRAFT FAILED: {e}")

    # Defense-in-depth: redlined files must never reach apply_change, even if a
    # bug routed one into `approved`. Fail loudly. (Decision C sanity check.)
    redline = config.get("redline_files", [])

    def _is_redlined(fp: str) -> bool:
        n = fp.replace("\\", "/")
        return any(n == r or n.endswith("/" + r) for r in redline)

    for prop in approved:
        prop_id = prop.get("id", "unknown")
        file_path = prop.get("file", "")

        if _is_redlined(file_path):
            msg = (f"REDLINE VIOLATION: approved proposal [{prop_id}] targets "
                   f"redlined file {file_path}. Refusing to edit it. This should "
                   f"have been converted to a decision draft in approve.py.")
            print(f"\n  !! {msg}")
            prop["status"] = "redline_blocked"
            prop["deploy_error"] = msg
            failed.append(prop)
            try:
                draft_rel = write_redline_draft(repo_root, prop, today)
                prop["draft_file"] = draft_rel
                committed_files.append(draft_rel)
                print(f"     Recovered: wrote decision draft -> {draft_rel}")
            except Exception as e:  # noqa: BLE001
                print(f"     Recovery failed: {e}")
            continue

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
    for prop in deployed + queued + rejected + failed + redline_drafts:
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
        if prop.get("redline_reason"):
            log_entry["reason"] = prop["redline_reason"]
        if prop.get("draft_file"):
            log_entry["draft_file"] = prop["draft_file"]

        log["entries"].append(log_entry)

    save_log(repo_root, log)
    committed_files.append("improvements/log.yaml")

    # Step 4: Git commit if anything was deployed or any redline draft written
    if deployed or redline_drafts:
        parts = []
        if deployed:
            summaries = [d.get("finding", d.get("change", "improvement"))[:60] for d in deployed]
            parts.append(f"{len(deployed)} improvement(s): " + "; ".join(summaries))
        if redline_drafts:
            parts.append(f"{len(redline_drafts)} linchpin-revision draft(s) for Grant")
        commit_msg = "[kaizen] " + " | ".join(parts)
        if len(commit_msg) > 120:
            commit_msg = commit_msg[:117] + "..."

        # Also add snapshots (only exist if something deployed)
        if deployed:
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

    print(f"\n  Deploy complete: {len(deployed)} deployed, {len(failed)} failed, {len(redline_drafts)} redline-draft(s)")
    print(f"  Tokens used: {total_tokens['input']} in / {total_tokens['output']} out")

    return {
        "deployed": deployed,
        "queued": queued,
        "rejected": rejected,
        "failed": failed,
        "redline_drafts": redline_drafts,
        "tokens": total_tokens,
    }


if __name__ == "__main__":
    repo_root = os.environ.get("REPO_ROOT", ".")
    results = run_deploy(repo_root)
    output_path = Path(repo_root) / "agents" / "kaizen-twilight" / "_deploy_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Results written to {output_path}")
