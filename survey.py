"""
Kaizen Twilight — Layer 1: Survey
Scans systems and collects 1% improvement findings using Claude Haiku.
"""

import glob
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import yaml


def load_config(repo_root: str) -> dict:
    config_path = Path(repo_root) / "agents" / "kaizen-twilight" / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def read_file(repo_root: str, path: str) -> str | None:
    full_path = Path(repo_root) / path
    if not full_path.exists():
        return None
    return full_path.read_text(encoding="utf-8", errors="replace")


def read_recent_signals(repo_root: str, days: int = 3) -> str:
    """Read signal files from the last N days."""
    signals_dir = Path(repo_root) / "signals"
    if not signals_dir.exists():
        return "No signals directory found."

    cutoff = datetime.now() - timedelta(days=days)
    entries = []

    for f in sorted(signals_dir.glob("*.md"), reverse=True):
        # Signal files are named like 2026-03-04-session-description.md
        name = f.stem
        try:
            date_str = "-".join(name.split("-")[:3])
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date >= cutoff:
                content = f.read_text(encoding="utf-8", errors="replace")
                entries.append(f"## {f.name}\n{content}")
        except (ValueError, IndexError):
            continue

    if not entries:
        return "No recent signals found."
    return "\n\n---\n\n".join(entries)


def read_skill_files(repo_root: str) -> str:
    """Read all SKILL.md files from .claude/skills/."""
    skills_dir = Path(repo_root) / ".claude" / "skills"
    if not skills_dir.exists():
        return "No skills directory found."

    entries = []
    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        skill_name = skill_file.parent.name
        content = skill_file.read_text(encoding="utf-8", errors="replace")
        # Truncate very long skills to save tokens
        if len(content) > 3000:
            content = content[:3000] + "\n... [truncated]"
        entries.append(f"## Skill: {skill_name}\n{content}")

    if not entries:
        return "No skill files found."
    return "\n\n---\n\n".join(entries)


def call_haiku(client: anthropic.Anthropic, model: str, system_prompt: str,
               content: str, scan_prompt: str) -> dict:
    """Send content to Haiku for a focused 1% improvement scan."""
    message = client.messages.create(
        model=model,
        max_tokens=500,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"{scan_prompt}\n\n---\n\nContent to analyze:\n\n{content}"
        }]
    )
    return {
        "response": message.content[0].text,
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
    }


SYSTEM_PROMPT = """You are Kaizen Twilight's survey scanner. Your job is to find ONE specific, actionable 1% improvement in the content you're given.

Rules:
- Be specific: name the exact file, line, value, or config that should change
- Be practical: only suggest changes that can be made with a file edit or config tweak
- Focus on: stale data, broken references, missing automation, misalignment with goals
- Skip: cosmetic changes, subjective rewording, adding comments or docs
- Output format: A short paragraph (2-4 sentences) describing the finding and what to change

If the content looks healthy and you can't find a real improvement, respond with exactly: NO_FINDING"""


def run_survey(repo_root: str) -> dict:
    """Run the full survey scan. Returns findings dict."""
    config = load_config(repo_root)
    client = anthropic.Anthropic()
    model = config["models"]["survey"]

    findings = []
    total_tokens = {"input": 0, "output": 0}

    print("=== Kaizen Twilight — Layer 1: Survey ===")
    print(f"Model: {model}")
    print()

    for target in config["survey"]["targets"]:
        path = target["path"]
        prompt = target["prompt"]
        target_type = target.get("type", "file")

        print(f"  Scanning: {path} ({target_type})")

        # Load content based on type
        if target_type == "directory_recent":
            content = read_recent_signals(repo_root, target.get("days", 3))
        elif target_type == "glob":
            content = read_skill_files(repo_root)
        else:
            content = read_file(repo_root, path)

        if not content:
            print(f"    -> Skipped (file not found or empty)")
            continue

        # Truncate to avoid blowing token budget
        if len(content) > 8000:
            content = content[:8000] + "\n... [truncated]"

        result = call_haiku(client, model, SYSTEM_PROMPT, content, prompt)
        total_tokens["input"] += result["input_tokens"]
        total_tokens["output"] += result["output_tokens"]

        response = result["response"].strip()
        if response == "NO_FINDING":
            print(f"    -> No finding (healthy)")
        else:
            print(f"    -> Finding recorded")
            findings.append({
                "source": path,
                "finding": response,
            })

    print()
    print(f"  Survey complete: {len(findings)} findings from {len(config['survey']['targets'])} targets")
    print(f"  Tokens used: {total_tokens['input']} in / {total_tokens['output']} out")

    return {
        "findings": findings,
        "tokens": total_tokens,
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    repo_root = os.environ.get("REPO_ROOT", ".")
    results = run_survey(repo_root)
    # Write results for next layer
    output_path = Path(repo_root) / "agents" / "kaizen-twilight" / "_survey_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results written to {output_path}")
