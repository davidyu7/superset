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
  3. Summarizes the final decision in SIP terms.
  4. Writes an ``architectural-decision`` knowledge note via the REST API
     so future triage sessions can find precedent.
"""

from __future__ import annotations

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


def _build_pr_context(linked_prs: list[dict[str, str]]) -> str:
    """Format linked PR info for the capture prompt."""
    if not linked_prs:
        return "No linked PRs found."

    lines = []
    for pr in linked_prs:
        status = "merged" if pr.get("merged") == "true" else pr.get("state", "unknown")
        lines.append(f"- PR #{pr['number']}: {pr['title']} ({status}) - {pr['url']}")
    return "\n".join(lines)


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

5. Create an ``architectural-decision`` knowledge note via the Devin
   knowledge API with:
   - ``name``: "architectural-decision:{repo}#{issue_number}: {{short_title}}"
   - ``trigger``: "Precedent for issues related to: {{topic_keywords}}"
   - ``pinned_repo``: "{repo}"
   - ``body``: The SIP-structured summary including both explicit and
     implicit decisions

6. The note should be findable by future triage sessions searching for
   ``architectural-decision`` in the knowledge base.
"""


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

    prompt = _build_capture_prompt(
        repo, issue_number, issue_title, issue_body, linked_prs
    )

    session = client.create_session(
        prompt=prompt,
        repos=[repo],
        tags=["decision-capture", f"issue-{issue_number}"],
        max_acu_limit=max_acu,
        title=f"Decision capture: {repo}#{issue_number}",
    )

    devin_id: str = session["devin_id"]
    session_url: str = session.get("url", f"https://app.devin.ai/sessions/{devin_id}")

    logger.info("Decision capture session started: %s", session_url)

    poll_interval: int = config.get("poll_interval_seconds", 30)
    poll_max: int = config.get("poll_max_attempts", 120)

    final = client.poll_session(devin_id, interval=poll_interval, max_attempts=poll_max)

    status = final.get("status", "unknown")
    detail = final.get("status_detail", "unknown")
    logger.info("Session ended: status=%s detail=%s", status, detail)


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
