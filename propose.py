"""
Kaizen Twilight — Layer 2: Propose
Takes survey findings and generates structured improvement proposals.
Deduplicates against the idea ledger (log.yaml).
"""

import json
import re
import os
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


def get_skip_summaries(log: dict, config: dict) -> list[str]:
    """Get summaries of ideas that should be skipped (recently deployed, rejected with active cooldown, or postponed not yet due)."""
    now = datetime.now()
    skip = []

    for entry in log.get("entries", []):
        status = entry.get("status", "")
        summary = entry.get("summary", "")

        # Open items waiting on Grant must not be re-proposed every night
        # (2026-10-27: voice.md had 10 near-identical queued copies).
        if status in ("queued", "proposed"):
            skip.append(summary)
            continue

        if status == "rejected":
            cooldown = entry.get("cooldown_until")
            if cooldown:
                cooldown_date = datetime.strptime(cooldown, "%Y-%m-%d")
                if now < cooldown_date:
                    skip.append(summary)

        elif status == "postponed":
            recheck = entry.get("recheck_date")
            if recheck:
                recheck_date = datetime.strptime(recheck, "%Y-%m-%d")
                if now < recheck_date:
                    skip.append(summary)

        elif status == "deployed":
            deployed_date_str = entry.get("date_resolved")
            if deployed_date_str:
                deployed_date = datetime.strptime(deployed_date_str, "%Y-%m-%d")
                cooldown_days = config.get("cooldown", {}).get("deployed_days", 14)
                if (now - deployed_date).days < cooldown_days:
                    skip.append(summary)

    return skip


SYSTEM_PROMPT = """You are Kaizen Twilight's proposal generator. Given survey findings, you generate structured improvement proposals.

For each finding, output a JSON array of objects with these fields:
- "finding": The original finding (1 sentence)
- "change": Specific change to make (what file, what to modify)
- "file": The file path to modify (relative to repo root)
- "effort": "trivial" | "small" | "medium"
  - trivial: config tweaks, typo fixes, updating stale data/values
  - small: rewriting a skill section, adjusting workflow logic, updating references
  - medium: new automations, architecture changes, anything touching voice/soul/CLAUDE.md
- "impact": Why this matters (1 sentence)
- "ember_check": true if this serves coaching + speaking path, false if pure ops
- "category": "fix" | "optimize" | "automate" | "align"

Rules:
- Be extremely specific about the file and change. Vague proposals are useless.
- If a finding doesn't translate to a concrete file change, skip it.
- If the file path is a directory (like signals/), the change should specify what new file or automation to create.
- Output ONLY the JSON array. No preamble, no markdown fences, no explanation.
- If no findings translate to proposals, output: []

SKIP these topics (already handled or on cooldown):
{skip_list}"""


PATH_RE = re.compile(r"(?:signals|reference|research|decisions|tools|agents|outputs|improvements|\.claude|\.github)/[\w./-]+\.(?:md|ya?ml|py|js|json|gs|html)")


def verify_proposals(repo_root: str, proposals: list, log: dict) -> list:
    """Drop proposals that cite files that do not exist, that only claim truncation,
    or that target a file which already has an open (queued) item. Added 2026-10-27."""
    root = Path(repo_root)
    open_files = {e.get("file") for e in log.get("entries", []) if e.get("status") in ("queued", "proposed")}
    kept = []
    for prop in proposals:
        text = " ".join(str(prop.get(k, "")) for k in ("finding", "change", "impact"))
        target = str(prop.get("file", "")).strip()
        reason = None
        if target and target in open_files:
            reason = f"open item already exists for {target}"
        elif target and not target.endswith("/") and not (root / target).exists() and "create" not in text.lower():
            reason = f"target file does not exist: {target}"
        elif re.search(r"truncat|cut off mid|mid-sentence", text, re.I):
            reason = "truncation claim (scanner artifact)"
        else:
            missing = [m for m in PATH_RE.findall(text) if m != target and not (root / m).exists()]
            if missing:
                reason = f"cites missing file(s): {missing}"
        if reason:
            print(f"  DROPPED (verify): {prop.get('finding', '')[:80]} -- {reason}")
        else:
            kept.append(prop)
    return kept


def run_propose(repo_root: str) -> dict:
    """Generate proposals from survey findings."""
    config = load_config(repo_root)
    log = load_log(repo_root)
    client = anthropic.Anthropic()
    model = config["models"]["propose"]
    today = datetime.now().strftime("%Y-%m-%d")

    # Load survey results
    survey_path = Path(repo_root) / "agents" / "kaizen-twilight" / "_survey_results.json"
    with open(survey_path) as f:
        survey = json.load(f)

    findings = survey.get("findings", [])
    if not findings:
        print("=== Kaizen Twilight — Layer 2: Propose ===")
        print("  No findings to propose on. Skipping.")
        result = {"proposals": [], "tokens": {"input": 0, "output": 0}}
        output_path = Path(repo_root) / "agents" / "kaizen-twilight" / "_proposals.json"
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        return result

    # Build skip list from log
    skip_summaries = get_skip_summaries(log, config)
    skip_text = "\n".join(f"- {s}" for s in skip_summaries) if skip_summaries else "(none)"

    # Format findings for the prompt
    findings_text = "\n\n".join(
        f"Source: {f['source']}\nFinding: {f['finding']}"
        for f in findings
    )

    print("=== Kaizen Twilight — Layer 2: Propose ===")
    print(f"  Model: {model}")
    print(f"  Findings to process: {len(findings)}")
    print(f"  Skip list entries: {len(skip_summaries)}")

    system = SYSTEM_PROMPT.format(skip_list=skip_text)

    message = client.messages.create(
        model=model,
        max_tokens=2000,
        system=system,
        messages=[{
            "role": "user",
            "content": f"Generate improvement proposals for these findings:\n\n{findings_text}"
        }]
    )

    response_text = message.content[0].text.strip()
    tokens = {
        "input": message.usage.input_tokens,
        "output": message.usage.output_tokens,
    }

    # Parse JSON response (strip markdown fences if present)
    clean_text = response_text
    if clean_text.startswith("```"):
        clean_text = "\n".join(clean_text.split("\n")[1:])
    if clean_text.endswith("```"):
        clean_text = "\n".join(clean_text.split("\n")[:-1])
    clean_text = clean_text.strip()

    try:
        proposals = json.loads(clean_text)
        if not isinstance(proposals, list):
            proposals = []
    except json.JSONDecodeError:
        print(f"  WARNING: Failed to parse proposal JSON. Raw response:\n{response_text}")
        proposals = []

    proposals = verify_proposals(repo_root, proposals, log)

    # Assign IDs
    for i, prop in enumerate(proposals, 1):
        prop["id"] = f"kaizen-{today}-{i:03d}"
        prop["date"] = today
        prop["status"] = "proposed"

    print(f"  Proposals generated: {len(proposals)}")
    print(f"  Tokens used: {tokens['input']} in / {tokens['output']} out")

    return {
        "proposals": proposals,
        "tokens": tokens,
    }


if __name__ == "__main__":
    repo_root = os.environ.get("REPO_ROOT", ".")
    results = run_propose(repo_root)
    output_path = Path(repo_root) / "agents" / "kaizen-twilight" / "_proposals.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Results written to {output_path}")
