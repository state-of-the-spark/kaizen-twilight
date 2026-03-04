"""
Kaizen Twilight — Layer 3: Approve
Auto-approves trivial/small changes. Queues medium+ for review.
Enforces safety rails on protected files.
"""

import json
import os
from pathlib import Path

import yaml


def load_config(repo_root: str) -> dict:
    config_path = Path(repo_root) / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def is_protected(file_path: str, config: dict) -> bool:
    """Check if a file is in the protected list."""
    protected = config.get("protected_files", [])
    normalized = file_path.replace("\\", "/")
    for p in protected:
        if normalized == p or normalized.endswith("/" + p):
            return True
    return False


def run_approve(repo_root: str) -> dict:
    """Review proposals and approve/queue them."""
    config = load_config(repo_root)
    auto_approve_levels = config.get("approval", {}).get("auto_approve", ["trivial"])
    max_deploys = config.get("max_auto_deploys", 3)

    # Load proposals
    proposals_path = Path(repo_root) / "_proposals.json"
    with open(proposals_path) as f:
        data = json.load(f)

    proposals = data.get("proposals", [])
    if not proposals:
        print("=== Kaizen Twilight — Layer 3: Approve ===")
        print("  No proposals to review. Skipping.")
        result = {"approved": [], "queued": [], "rejected": []}
        output_path = Path(repo_root) / "_approved.json"
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        return result

    print("=== Kaizen Twilight — Layer 3: Approve ===")
    print(f"  Proposals to review: {len(proposals)}")
    print(f"  Auto-approve levels: {auto_approve_levels}")
    print(f"  Max auto-deploys: {max_deploys}")

    approved = []
    queued = []
    rejected = []
    auto_deploy_count = 0

    for prop in proposals:
        prop_id = prop.get("id", "unknown")
        effort = prop.get("effort", "medium")
        file_path = prop.get("file", "")

        # Safety rail: protected files always queue
        if is_protected(file_path, config):
            prop["status"] = "queued"
            prop["queue_reason"] = "Protected file — requires your review"
            queued.append(prop)
            print(f"  [{prop_id}] QUEUED — protected file: {file_path}")
            continue

        # Safety rail: max auto-deploys
        if auto_deploy_count >= max_deploys:
            prop["status"] = "queued"
            prop["queue_reason"] = "Max auto-deploys reached for tonight"
            queued.append(prop)
            print(f"  [{prop_id}] QUEUED — deploy limit reached")
            continue

        # Check effort level
        if effort in auto_approve_levels:
            prop["status"] = "approved"
            approved.append(prop)
            auto_deploy_count += 1
            print(f"  [{prop_id}] APPROVED — effort: {effort}, file: {file_path}")
        else:
            prop["status"] = "queued"
            prop["queue_reason"] = f"Effort level '{effort}' requires your review"
            queued.append(prop)
            print(f"  [{prop_id}] QUEUED — effort: {effort}")

    # Validate approved proposals have required fields
    valid_approved = []
    for prop in approved:
        if not prop.get("file"):
            prop["status"] = "rejected"
            prop["reject_reason"] = "No file path specified"
            rejected.append(prop)
            print(f"  [{prop.get('id')}] REJECTED — missing file path")
        elif not prop.get("change"):
            prop["status"] = "rejected"
            prop["reject_reason"] = "No change description specified"
            rejected.append(prop)
            print(f"  [{prop.get('id')}] REJECTED — missing change description")
        else:
            valid_approved.append(prop)

    print(f"\n  Results: {len(valid_approved)} approved, {len(queued)} queued, {len(rejected)} rejected")

    result = {
        "approved": valid_approved,
        "queued": queued,
        "rejected": rejected,
    }

    # Write results
    output_path = Path(repo_root) / "_approved.json"
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"  Results written to {output_path}")

    return result


if __name__ == "__main__":
    repo_root = os.environ.get("REPO_ROOT", ".")
    run_approve(repo_root)
