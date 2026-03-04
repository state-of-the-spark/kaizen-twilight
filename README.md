# Kaizen Twilight — Nightly 1% Improvement Agent

AI that optimizes your business while you sleep.

---

## What It Does

Kaizen Twilight is a 5-layer Python pipeline that runs as a GitHub Action every night. It scans the files in your repo, finds one specific improvement in each file, proposes structured changes, auto-deploys safe ones, and emails you a morning summary of what happened.

The name comes from the Japanese concept of kaizen — continuous improvement through small, consistent changes. One percent better every day.

---

## How It Works

Each night the pipeline runs five layers in sequence:

**Layer 1 — Survey:** Claude Haiku reads the files you specify in `config.yaml` and looks for one concrete, actionable improvement in each. It outputs findings as short paragraphs naming the exact file, line, or value to change.

**Layer 2 — Propose:** Claude Sonnet takes those findings and generates structured proposals — each with a file path, a specific change description, an effort level (trivial / small / medium), and an impact statement.

**Layer 3 — Approve:** The approval engine applies your rules from `config.yaml`. Trivial and small changes are auto-approved up to the nightly limit. Protected files and medium-effort changes go to a review queue. No surprises.

**Layer 4 — Deploy:** Approved changes are applied by Claude Sonnet, which reads the original file and outputs the minimally-modified version. Every file is snapshotted before modification. All results are logged to `improvements/log.yaml`.

**Layer 5 — Report:** A markdown report is written to `decisions/`, and an HTML email is sent to your inbox with a summary of what was deployed and what needs your review.

---

## Quick Start

1. Fork this repo.

2. Copy `config.example.yaml` to `config.yaml` and customize which files to scan.

3. Add two GitHub secrets (Settings > Secrets and variables > Actions):
   - `ANTHROPIC_API_KEY` — get yours at console.anthropic.com
   - `GMAIL_APP_PASSWORD` — get yours at Google Account > Security > App passwords (requires 2FA enabled)

4. Push. The Action runs nightly at 4:00 UTC (configurable in the workflow file), or trigger it manually from the Actions tab.

5. Check your email in the morning.

---

## Configuration

All behavior is controlled by `config.yaml` (copied from `config.example.yaml`). The file is gitignored so your personal settings stay local.

### survey.targets

A list of files or directories to scan each night. Each target has:

- `path` — file or directory path relative to repo root
- `prompt` — what to look for (be specific about your goals)
- `type` — omit for single files, use `directory_recent` to scan a folder for recent files

### protected_files

Files that will never be auto-deployed, no matter the effort level. Changes to these files always go to the review queue. Use this for anything that defines who you are or how your business runs.

### max_auto_deploys

Maximum number of changes deployed in a single night. Defaults to 3. Prevents runaway overnight edits.

### approval

Controls which effort levels are auto-approved versus queued for review:

- `trivial` — config tweaks, typo fixes, updating stale values
- `small` — rewriting a section, adjusting workflow logic
- `medium` — new automations, architecture changes, anything touching identity or core config

### cooldown

Prevents the same idea from being proposed repeatedly:

- `rejected_days` — how long to suppress a rejected idea (default: 30 days)
- `deployed_days` — how long before a deployed improvement can be re-proposed (default: 14 days)

### email

Set `enabled: true` and configure your Gmail address and App Password. The subject line uses `{count}` as a placeholder for the number of improvements.

### models

You can swap models if needed, but the defaults are cost-optimized:

- Survey uses Haiku — fast, cheap, pattern-matching
- Propose and Report use Sonnet — needs synthesis

---

## Safety Rails

Kaizen Twilight is designed to be conservative by default:

- Protected files are never auto-deployed
- The nightly deploy limit caps how much changes at once
- Every modified file is snapshotted before the change is applied (in `improvements/snapshots/`)
- If a change produces identical content, it is rejected rather than committed
- If a change fails to apply, the original file is untouched and the failure is logged

---

## Cost

A typical nightly run costs approximately $0.17 in Claude API usage, or about $5 per month. This varies based on how many files you scan and their size.

---

## Reviewing Queued Items

Queued items appear in your morning email and in `improvements/log.yaml`. Each entry has a `status` field:

- `queued` — waiting for your review
- `deployed` — already applied
- `rejected` — skipped (with reason)
- `postponed` — deferred until a recheck date

To approve or reject a queued item, open `improvements/log.yaml`, change the status, and either apply the change manually or update the status to `rejected` with a note. The cooldown system will prevent it from being re-proposed until the cooldown period expires.

---

## Built By

State of the Spark — part of the SparkMyAI ecosystem, an AI onboarding community for small business owners. Learn more at sparkmyai.com (coming soon).
