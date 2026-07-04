# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Issue triage via the Devin Sessions API.

Called by ``.github/workflows/devin-issue-triage.yml``.

Flow:
  1. Start a *scoping* session with ``structured_output_schema`` that returns
     a JSON verdict (decision, confidence, reasoning, ...).
  2. Poll until the session finishes.
  3. Branch on the verdict:
     - ``autonomous`` (confidence >= threshold): start an implementation session.
     - ``needs_discussion``: post a SIP-structured comment, apply label.
  4. Upsert a bot status comment on the issue (hidden-marker pattern from
     ``superset-translations-comment.yml``).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
from typing import Any

import requests
import yaml
from devin_client import DevinClient, DevinClientError

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"

# ── Structured output JSON Schema (Draft 7) ──────────────────────────────

TRIAGE_VERDICT_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "TriageVerdict",
    "type": "object",
    "required": [
        "decision",
        "confidence",
        "reasoning",
        "affected_areas",
        "cited_constraints",
    ],
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["autonomous", "needs_discussion"],
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
        "reasoning": {"type": "string"},
        "affected_areas": {
            "type": "array",
            "items": {"type": "string"},
        },
        "cited_constraints": {
            "type": "array",
            "items": {"type": "string"},
        },
        "similar_prior_decisions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "additionalProperties": False,
}


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _build_triage_prompt(
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_body: str,
    maintainer_team: str,
) -> str:
    """Compose the prompt for the scoping session.

    Injects maintainer-intent constraints from AGENTS.md, SECURITY.md, and
    the SIP template, referenced by repo-relative path so they resolve in
    any fork.
    """
    return f"""\
You are triaging issue #{issue_number} in {repo}.

## Issue
**Title:** {issue_title}
**Body:**
{issue_body}

## Instructions
1. Read the following repo-relative files for hard constraints:
   - `AGENTS.md` (coding standards, refactor rules, pre-commit requirement)
   - `SECURITY.md` (security model, trust boundaries, vulnerability scope)
   - `.github/ISSUE_TEMPLATE/sip.md` (SIP vocabulary for discussion items)

2. SEARCH the knowledge base for notes with category prefix
   `architectural-decision:` to find precedent on similar past decisions.

3. Evaluate whether this issue can be autonomously fixed (code change + PR)
   or requires maintainer discussion (architectural, SIP-scale, ambiguous
   security finding).

4. Security triage rule (from AGENTS.md): a security finding is only
   autonomously actionable if it names the specific `SECURITY.md`
   role/capability-matrix row violated AND the assumed principal.
   Otherwise route to ``needs_discussion``.

5. Provide your verdict as structured output with the required schema.
   - ``decision``: "autonomous" or "needs_discussion"
   - ``confidence``: 0.0 to 1.0
   - ``reasoning``: concise explanation
   - ``affected_areas``: list of repo areas affected
   - ``cited_constraints``: which AGENTS.md / SECURITY.md rules apply
   - ``similar_prior_decisions``: any matching architectural-decision notes

## Constraints
- Every PR MUST run `pre-commit run --all-files` and fix issues before pushing.
- Obey the "What NOT to Do" refactor rules in AGENTS.md.
- @{maintainer_team} must be consulted for SIP-scale changes.
"""


def _build_implementation_prompt(
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_body: str,
    triage_reasoning: str,
    maintainer_team: str,
) -> str:
    return f"""\
Fix issue #{issue_number} in {repo}.

## Issue
**Title:** {issue_title}
**Body:**
{issue_body}

## Triage reasoning
{triage_reasoning}

## Hard constraints (read the full files for details)
- Read `AGENTS.md` and follow ALL rules (pre-commit, no `any` types, no new JS
  files, use `@superset-ui/core` wrappers, antd tokens, Python type hints,
  prefer UUIDs).
- Read `SECURITY.md` before touching any security-related code.
- MUST run `pre-commit run --all-files` and fix all issues before pushing.
- PR title must follow Conventional Commits: `type(scope): description`.
- PR must include `Closes #{issue_number}` in the body.
- @{maintainer_team} is the maintainer team.
"""


def _build_discussion_comment(
    verdict: dict[str, Any],
    session_url: str,
    maintainer_team: str,
) -> str:
    """Format a SIP-vocabulary discussion comment."""
    reasoning = verdict.get("reasoning", "")
    areas = ", ".join(verdict.get("affected_areas", []))
    constraints = ", ".join(verdict.get("cited_constraints", []))
    prior = verdict.get("similar_prior_decisions", [])
    prior_text = "\n".join(f"- {d}" for d in prior) if prior else "None found."

    return f"""\
## Devin Triage: Needs Discussion

This issue has been triaged by Devin and requires maintainer input before
proceeding. cc @{maintainer_team}

### Motivation
{reasoning}

### Affected Areas
{areas}

### Cited Constraints
{constraints}

### Similar Prior Decisions
{prior_text}

### Proposed Change
_Pending maintainer discussion._

### New or Changed Public Interfaces
_To be determined during discussion._

### New dependencies
_None identified._

### Migration Plan and Compatibility
_To be determined during discussion._

### Rejected Alternatives
_None at this stage._

---
Confidence: {verdict.get("confidence", "N/A")}
Devin session: {session_url}
"""


def _build_status_comment(
    marker: str,
    verdict: dict[str, Any] | None,
    session_url: str,
    pr_url: str | None,
    phase: str,
) -> str:
    """Build a bot status comment with hidden marker for upsert."""
    parts = [marker]
    parts.append(f"### Devin Bot Status: {phase}")
    parts.append(f"- **Session:** {session_url}")
    if verdict:
        parts.append(f"- **Decision:** {verdict.get('decision', 'unknown')}")
        parts.append(f"- **Confidence:** {verdict.get('confidence', 'N/A')}")
        parts.append(f"- **Reasoning:** {verdict.get('reasoning', 'N/A')}")
    if pr_url:
        parts.append(f"- **PR:** {pr_url}")
    return "\n".join(parts)


def _find_existing_prs(
    repo: str,
    issue_number: int,
    token: str,
) -> list[dict[str, str]]:
    """Search for open PRs that reference this issue."""
    if not token:
        return []

    headers = _gh_headers(token)
    existing: list[dict[str, str]] = []

    # Check issue timeline for cross-referenced PRs
    url = f"{_GH_API}/repos/{repo}/issues/{issue_number}/timeline"
    page = 1
    while True:
        resp = requests.get(
            url,
            headers={
                **headers,
                "Accept": "application/vnd.github.mockingbird-preview+json",
            },
            params={"per_page": 100, "page": page},
            timeout=30,
        )
        if not resp.ok:
            break
        events = resp.json()
        if not events:
            break
        for event in events:
            if event.get("event") == "cross-referenced":
                source = event.get("source", {})
                issue_data = source.get("issue", {})
                pr_info = issue_data.get("pull_request", {})
                if pr_info and issue_data.get("state") == "open":
                    existing.append(
                        {
                            "number": str(issue_data.get("number", "")),
                            "title": issue_data.get("title", ""),
                            "url": issue_data.get("html_url", ""),
                        }
                    )
        if len(events) < 100:
            break
        page += 1

    return existing


def _build_existing_pr_context(existing_prs: list[dict[str, str]]) -> str:
    """Format existing PR info for injection into the triage prompt."""
    if not existing_prs:
        return ""

    lines = [
        "\n## Existing PRs addressing this issue",
        "NOTE: The following open PRs already reference this issue:",
    ]
    for pr in existing_prs:
        lines.append(f"- PR #{pr['number']}: {pr['title']} ({pr['url']})")
    lines.append(
        "\nConsider whether this issue is already being addressed. "
        "If an existing PR looks adequate, recommend `needs_discussion` "
        "with reasoning that a PR already exists and needs review rather "
        "than starting a new implementation."
    )
    return "\n".join(lines)


def _get_knowledge_ids(client: DevinClient, repo: str) -> list[str]:
    """Fetch note IDs for triage-policy notes pinned to this repo."""
    try:
        notes = client.list_knowledge_notes(
            search="triage-policy",
            pinned_repo=repo,
        )
        return [n["note_id"] for n in notes if "note_id" in n]
    except DevinClientError:
        logger.warning("Could not fetch triage-policy knowledge notes")
        return []


def run_triage(
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_body: str,
    github_token: str,
) -> None:
    """Orchestrate the full triage flow."""
    config = load_config()
    client = DevinClient()

    maintainer_team: str = config.get("maintainer_team", "apache/superset-committers")
    labels = config.get("labels", {})
    marker: str = config.get("comment_marker", "<!-- devin-bot -->")
    threshold: float = config.get("confidence_threshold", 0.7)
    poll_interval: int = config.get("poll_interval_seconds", 30)
    poll_max: int = config.get("poll_max_attempts", 120)
    max_acu: int = config.get("max_acu_limit", 10)
    playbook_id: str = config.get("playbook_id", "")

    knowledge_ids = _get_knowledge_ids(client, repo)

    # ── 0. Check for existing PRs ────────────────────────────────────────
    existing_prs = _find_existing_prs(repo, issue_number, github_token)
    pr_context = _build_existing_pr_context(existing_prs)
    if existing_prs:
        logger.info(
            "Found %d existing open PR(s) for issue #%d",
            len(existing_prs),
            issue_number,
        )

    # ── 1. Start scoping session ─────────────────────────────────────────
    prompt = _build_triage_prompt(
        repo, issue_number, issue_title, issue_body, maintainer_team
    )
    if pr_context:
        prompt += pr_context

    create_kwargs: dict[str, Any] = {
        "prompt": prompt,
        "repos": [repo],
        "structured_output_schema": TRIAGE_VERDICT_SCHEMA,
        "structured_output_required": True,
        "tags": ["triage", f"issue-{issue_number}"],
        "max_acu_limit": max_acu,
        "title": f"Triage: {repo}#{issue_number}",
    }
    if playbook_id:
        create_kwargs["playbook_id"] = playbook_id
    if knowledge_ids:
        create_kwargs["knowledge_ids"] = knowledge_ids

    session = client.create_session(**create_kwargs)
    devin_id: str = session["devin_id"]
    session_url: str = session.get("url", f"https://app.devin.ai/sessions/{devin_id}")

    logger.info("Scoping session started: %s", session_url)

    _upsert_issue_comment(
        repo,
        issue_number,
        _build_status_comment(marker, None, session_url, None, "Triage in progress"),
        marker,
        github_token,
    )

    # ── 2. Poll ──────────────────────────────────────────────────────────
    final = client.poll_session(devin_id, interval=poll_interval, max_attempts=poll_max)

    verdict: dict[str, Any] | None = final.get("structured_output")

    if not verdict:
        logger.error("Session ended without structured output")
        _upsert_issue_comment(
            repo,
            issue_number,
            _build_status_comment(
                marker, None, session_url, None, "Triage failed (no verdict)"
            ),
            marker,
            github_token,
        )
        sys.exit(1)

    logger.info("Verdict: %s", json.dumps(verdict, indent=2))

    decision: str = verdict.get("decision", "needs_discussion")
    confidence: float = verdict.get("confidence", 0.0)

    # ── 3. Branch ────────────────────────────────────────────────────────
    if decision == "autonomous" and confidence >= threshold:
        _handle_autonomous(
            client,
            config,
            repo,
            issue_number,
            issue_title,
            issue_body,
            verdict,
            session_url,
            marker,
            github_token,
            maintainer_team,
            max_acu,
            playbook_id,
        )
    else:
        _handle_needs_discussion(
            client,
            repo,
            issue_number,
            verdict,
            session_url,
            marker,
            labels,
            github_token,
            maintainer_team,
        )


def _handle_autonomous(
    client: DevinClient,
    config: dict[str, Any],
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_body: str,
    verdict: dict[str, Any],
    triage_session_url: str,
    marker: str,
    github_token: str,
    maintainer_team: str,
    max_acu: int,
    playbook_id: str,
) -> None:
    """Start an implementation session and track it."""
    prompt = _build_implementation_prompt(
        repo,
        issue_number,
        issue_title,
        issue_body,
        verdict.get("reasoning", ""),
        maintainer_team,
    )

    create_kwargs: dict[str, Any] = {
        "prompt": prompt,
        "repos": [repo],
        "tags": ["implementation", f"issue-{issue_number}"],
        "max_acu_limit": max_acu,
        "title": f"Fix: {repo}#{issue_number}",
    }
    if playbook_id:
        create_kwargs["playbook_id"] = playbook_id

    impl_session = client.create_session(**create_kwargs)
    impl_id: str = impl_session["devin_id"]
    impl_url: str = impl_session.get("url", f"https://app.devin.ai/sessions/{impl_id}")

    logger.info("Implementation session started: %s", impl_url)

    _upsert_issue_comment(
        repo,
        issue_number,
        _build_status_comment(
            marker, verdict, impl_url, None, "Implementation in progress"
        ),
        marker,
        github_token,
    )

    poll_interval: int = config.get("poll_interval_seconds", 30)
    poll_max: int = config.get("poll_max_attempts", 120)
    labels = config.get("labels", {})

    final = client.poll_session(impl_id, interval=poll_interval, max_attempts=poll_max)
    pull_requests: list[dict[str, Any]] = final.get("pull_requests", [])
    pr_url = pull_requests[0].get("pr_url") if pull_requests else None

    phase = "PR opened" if pr_url else "Implementation complete"
    _upsert_issue_comment(
        repo,
        issue_number,
        _build_status_comment(marker, verdict, impl_url, pr_url, phase),
        marker,
        github_token,
    )

    if pr_url:
        _add_label(
            repo, issue_number, labels.get("pr_opened", "devin:pr-opened"), github_token
        )


def _handle_needs_discussion(
    client: DevinClient,
    repo: str,
    issue_number: int,
    verdict: dict[str, Any],
    session_url: str,
    marker: str,
    labels: dict[str, str],
    github_token: str,
    maintainer_team: str,
) -> None:
    """Post SIP-structured comment and apply label."""
    comment_body = _build_discussion_comment(verdict, session_url, maintainer_team)

    _post_issue_comment(repo, issue_number, comment_body, github_token)

    _upsert_issue_comment(
        repo,
        issue_number,
        _build_status_comment(marker, verdict, session_url, None, "Needs discussion"),
        marker,
        github_token,
    )

    _add_label(
        repo,
        issue_number,
        labels.get("needs_discussion", "devin:needs-discussion"),
        github_token,
    )


# ── GitHub helpers (use GITHUB_TOKEN, not Devin) ─────────────────────────

_GH_API = "https://api.github.com"


def _gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _upsert_issue_comment(
    repo: str,
    issue_number: int,
    body: str,
    marker: str,
    token: str,
) -> None:
    """Create-or-update a comment identified by a hidden marker."""
    url = f"{_GH_API}/repos/{repo}/issues/{issue_number}/comments"
    headers = _gh_headers(token)

    page = 1
    while True:
        resp = requests.get(
            url,
            headers=headers,
            params={"per_page": 100, "page": page},
            timeout=30,
        )
        if not resp.ok:
            break
        comments = resp.json()
        if not comments:
            break
        for comment in comments:
            if marker in comment.get("body", ""):
                update_url = f"{_GH_API}/repos/{repo}/issues/comments/{comment['id']}"
                requests.patch(
                    update_url,
                    headers=headers,
                    json={"body": body},
                    timeout=30,
                )
                logger.info("Updated status comment %s", comment["id"])
                return
        if len(comments) < 100:
            break
        page += 1

    requests.post(url, headers=headers, json={"body": body}, timeout=30)
    logger.info("Created status comment on #%d", issue_number)


def _post_issue_comment(repo: str, issue_number: int, body: str, token: str) -> None:
    url = f"{_GH_API}/repos/{repo}/issues/{issue_number}/comments"
    requests.post(url, headers=_gh_headers(token), json={"body": body}, timeout=30)


def _add_label(repo: str, issue_number: int, label: str, token: str) -> None:
    url = f"{_GH_API}/repos/{repo}/issues/{issue_number}/labels"
    requests.post(url, headers=_gh_headers(token), json={"labels": [label]}, timeout=30)


# ── CLI entry point ──────────────────────────────────────────────────────


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    issue_number_str = os.environ.get("ISSUE_NUMBER", "0")
    issue_title = os.environ.get("ISSUE_TITLE", "")
    issue_body = os.environ.get("ISSUE_BODY", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")

    if not repo or not github_token:
        logger.error("GITHUB_REPOSITORY and GITHUB_TOKEN are required")
        sys.exit(1)

    issue_number = int(issue_number_str)
    if issue_number <= 0:
        logger.error("ISSUE_NUMBER must be a positive integer")
        sys.exit(1)

    run_triage(repo, issue_number, issue_title, issue_body, github_token)


if __name__ == "__main__":
    main()
