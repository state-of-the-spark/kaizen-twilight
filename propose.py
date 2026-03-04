"""
Kaizen Twilight — Layer 2: Propose
Takes survey findings and generates structured improvement proposals.
Deduplicates against the idea ledger (log.yaml).
"""

import json
import os
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


def get_skip_summaries(log: dict, config: dict) -> list[str]:
    """Get summaries of ideas that should be skipped (recently deployed, rejected with active cooldown, or postponed not yet due)."""
    now = datetime.now()
    skip = []

    for entry in log.get("entries", []):
        status = entry.get("status", "")
        summary = entry.get("summary", "")

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
  - small: rewriting a section, adjusting workflow logic, updating references
  - medium: new automations, architecture changes, anything touching core config or identity files
- "impact": Why this matters (1 sentence)
- "category": "fix" | "optimize" | "automate" | "align"

Rules:
- Be extremely specific about the file and change. Vague proposals are useless.
- If a finding doesn't translate to a concrete file change, skip it.
- If the file path is a directory (like notes/), the change should specify what new file or automation to create.
- Output ONLY the JSON array. No preamble, no markdown fences, no explanation.
- If no findings translate to proposals, output: []

SKIP these topics (already handled or on cooldown):
{skip_list}"""


def run_propose(repo_root: str) -> dict:
    """Generate proposals from survey findings."""
    config = load_config(repo_root)
    log = load_log(repo_root)
    client = anthropic.Anthropic()
    model = config["models"]["propose"]
    today = datetime.now().strftime("%Y-%m-%d")

    # Load survey results
    survey_path = Path(repo_root) / "_survey_results.json"
    with open(survey_path) as f:
        survey = json.load(f)

    findings = survey.get("findings", [])
    if not findings:
        print("=== Kaizen Twilight — Layer 2: Propose ===")
        print("  No findings to propose on. Skipping.")
        result = {"proposals": [], "tokens": {"input": 0, "output": 0}}
        output_path = Path(repo_root) / "_proposals.json"
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
    output_path = Path(repo_root) / "_proposals.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Results written to {output_path}")
