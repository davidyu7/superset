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
"""Architectural decision capture on ``devin:needs-discussion`` issue close.

Called by ``.github/workflows/devin-decision-capture.yml``.

Starts a Devin session that:
  1. Reads the closed issue + its discussion thread.
  2. Finds linked/merged PRs and reads their diffs and review comments
     to capture implicit decisions from the code itself.
  3. Returns the decision as **structured output** (JSON).
  4. The script then creates the ``architectural-decision`` knowledge note
     via the REST API directly -- no in-session approval needed.
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
from devin_client import DevinClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


_GH_API = "https://api.github.com"


def _gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _find_linked_prs(
    repo: str,
    issue_number: int,
    token: str,
) -> list[dict[str, str]]:
    """Find PRs linked to an issue via the timeline API."""
    linked: list[dict[str, str]] = []
    if not token:
        return linked

    headers = _gh_headers(token)
    # Use timeline events to find cross-referenced PRs
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
                if pr_info:
                    linked.append(
                        {
                            "number": str(issue_data.get("number", "")),
                            "title": issue_data.get("title", ""),
                            "url": issue_data.get("html_url", ""),
                            "state": issue_data.get("state", ""),
                            "merged": "true" if pr_info.get("merged_at") else "false",
                        }
                    )
        if len(events) < 100:
            break
        page += 1

    return linked


def _get_pr_files(
    repo: str,
    pr_number: str,
    token: str,
) -> list[str]:
    """Return the list of file paths changed by a PR."""
    headers = _gh_headers(token)
    url = f"{_GH_API}/repos/{repo}/pulls/{pr_number}/files"
    files: list[str] = []
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
        items = resp.json()
        if not items:
            break
        files.extend(f.get("filename", "") for f in items)
        if len(items) < 100:
            break
        page += 1
    return files


def _is_automation_only(
    repo: str,
    linked_prs: list[dict[str, str]],
    exclude_prefixes: list[str],
    token: str,
) -> bool:
    """Return True if every linked PR touches only excluded paths."""
    if not linked_prs or not token or not exclude_prefixes:
        return False

    for pr in linked_prs:
        files = _get_pr_files(repo, pr["number"], token)
        if not files:
            return False
        for f in files:
            if not any(f.startswith(p) for p in exclude_prefixes):
                return False
    return True


def _build_pr_context(linked_prs: list[dict[str, str]]) -> str:
    """Format linked PR info for the capture prompt."""
    if not linked_prs:
        return "No linked PRs found."

    lines = []
    for pr in linked_prs:
        status = "merged" if pr.get("merged") == "true" else pr.get("state", "unknown")
        lines.append(f"- PR #{pr['number']}: {pr['title']} ({status}) - {pr['url']}")
    return "\n".join(lines)


DECISION_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": [
        "short_title",
        "topic_keywords",
        "motivation",
        "proposed_change",
        "new_interfaces",
        "new_dependencies",
        "migration_plan",
        "rejected_alternatives",
        "implicit_decisions",
    ],
    "properties": {
        "short_title": {
            "type": "string",
            "description": (
                "Concise slug for the decision (e.g. presto-parameter-validation)"
            ),
        },
        "topic_keywords": {
            "type": "string",
            "description": (
                "Comma-separated keywords for future"
                " search (e.g. Presto, SQL injection)"
            ),
        },
        "motivation": {"type": "string"},
        "proposed_change": {"type": "string"},
        "new_interfaces": {"type": "string"},
        "new_dependencies": {"type": "string"},
        "migration_plan": {"type": "string"},
        "rejected_alternatives": {"type": "string"},
        "implicit_decisions": {
            "type": "string",
            "description": (
                "Architectural decisions implied by"
                " code changes, review comments,"
                " or patterns chosen in linked PRs"
            ),
        },
    },
}


def _build_capture_prompt(
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_body: str,
    linked_prs: list[dict[str, str]],
) -> str:
    pr_context = _build_pr_context(linked_prs)
    pr_instructions = ""
    if linked_prs:
        pr_numbers = ", ".join(f"#{pr['number']}" for pr in linked_prs)
        pr_instructions = f"""
3. Read the linked PRs ({pr_numbers}) -- for each one:
   a. Read the PR description and diff to understand what code changes
      were actually made (these encode implicit decisions).
   b. Read the PR review comments -- reviewers often explain WHY a
      particular approach was chosen over alternatives.
   c. Extract implicit architectural decisions from the code patterns:
      - Which layer was the fix applied at? (API, DAO, model, frontend)
      - What validation/security patterns were used?
      - Were existing utilities/helpers reused or new ones created?
      - What testing approach was chosen?

"""
    else:
        pr_instructions = """
3. No linked PRs were found. Focus on the discussion thread for
   decisions made.

"""

    return f"""\
Capture the architectural decision from issue #{issue_number} in {repo}.

## Issue
**Title:** {issue_title}
**Body:**
{issue_body}

## Linked PRs
{pr_context}

## Instructions
1. Read the full discussion thread on issue #{issue_number}.

2. Summarize the final decision using SIP vocabulary:
   - **Motivation**: what problem was discussed
   - **Proposed Change**: what was agreed upon
   - **New or Changed Public Interfaces**: any API changes
   - **New dependencies**: any new packages
   - **Migration Plan and Compatibility**: upgrade path
   - **Rejected Alternatives**: what was considered and rejected
{pr_instructions}
4. Combine EXPLICIT decisions (from discussion) and IMPLICIT decisions
   (from code changes, review comments, patterns chosen) into a single
   coherent summary.

5. Return your findings via the structured output tool. Do NOT create
   any knowledge notes yourself -- the caller will handle that.
   Do NOT ask any clarifying questions -- just do your best with the
   information available.
"""


def _build_note_body(decision: dict[str, Any]) -> str:
    """Format the structured decision output as a SIP-structured note body."""
    sections = [
        ("Motivation", decision.get("motivation", "N/A")),
        ("Proposed Change", decision.get("proposed_change", "N/A")),
        ("New or Changed Public Interfaces", decision.get("new_interfaces", "N/A")),
        ("New Dependencies", decision.get("new_dependencies", "N/A")),
        ("Migration Plan and Compatibility", decision.get("migration_plan", "N/A")),
        ("Rejected Alternatives", decision.get("rejected_alternatives", "N/A")),
        (
            "Implicit Decisions (from code/reviews)",
            decision.get("implicit_decisions", "N/A"),
        ),
    ]
    return "\n\n".join(f"## {title}\n{body}" for title, body in sections)


def run_capture(
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_body: str,
    github_token: str = "",
) -> None:
    """Start a session to capture the architectural decision."""
    config = load_config()
    client = DevinClient()

    max_acu: int = config.get("max_acu_limit", 10)

    linked_prs = _find_linked_prs(repo, issue_number, github_token)
    if linked_prs:
        logger.info(
            "Found %d linked PR(s) for issue #%d",
            len(linked_prs),
            issue_number,
        )

    exclude_prefixes: list[str] = config.get("decision_capture_exclude_paths", [])
    if _is_automation_only(repo, linked_prs, exclude_prefixes, github_token):
        logger.info(
            "All linked PRs touch only automation paths — skipping decision capture"
        )
        return

    prompt = _build_capture_prompt(
        repo, issue_number, issue_title, issue_body, linked_prs
    )

    session = client.create_session(
        prompt=prompt,
        repos=[repo],
        structured_output_schema=DECISION_OUTPUT_SCHEMA,
        structured_output_required=True,
        tags=["decision-capture", f"issue-{issue_number}"],
        max_acu_limit=max_acu,
        title=f"Decision capture: {repo}#{issue_number}",
        bypass_approval=True,
    )

    devin_id = session.get("session_id")
    if not devin_id:
        logger.error(
            "create_session returned no session_id. Response keys: %s",
            list(session.keys()),
        )
        sys.exit(1)
    session_url: str = session.get("url", f"https://app.devin.ai/sessions/{devin_id}")

    logger.info("Decision capture session started: %s", session_url)

    poll_interval: int = config.get("poll_interval_seconds", 30)
    poll_max: int = config.get("poll_max_attempts", 120)

    final = client.poll_session(devin_id, interval=poll_interval, max_attempts=poll_max)

    status = final.get("status", "unknown")
    detail = final.get("status_detail", "unknown")
    logger.info("Session ended: status=%s detail=%s", status, detail)

    # ── Extract structured output and create knowledge note via REST ───
    decision: dict[str, Any] | None = final.get("structured_output")
    if not decision:
        logger.error("Session ended without structured output")
        sys.exit(1)

    logger.info("Decision output: %s", json.dumps(decision, indent=2))

    short_title: str = decision.get("short_title", "unknown")
    topic_keywords: str = decision.get("topic_keywords", "")
    note_body = _build_note_body(decision)

    note_name = f"architectural-decision:{repo}#{issue_number}: {short_title}"
    note_trigger = f"Precedent for issues related to: {topic_keywords}"

    note = client.create_knowledge_note(
        name=note_name,
        body=note_body,
        trigger=note_trigger,
        pinned_repo=repo,
    )
    note_id = note.get("note_id", note.get("id", "unknown"))
    logger.info("Knowledge note created: %s (id=%s)", note_name, note_id)


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    issue_number_str = os.environ.get("ISSUE_NUMBER", "0")
    issue_title = os.environ.get("ISSUE_TITLE", "")
    issue_body = os.environ.get("ISSUE_BODY", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")

    if not repo:
        logger.error("GITHUB_REPOSITORY is required")
        sys.exit(1)

    issue_number = int(issue_number_str)
    if issue_number <= 0:
        logger.error("ISSUE_NUMBER must be a positive integer")
        sys.exit(1)

    run_capture(repo, issue_number, issue_title, issue_body, github_token)


if __name__ == "__main__":
    main()
