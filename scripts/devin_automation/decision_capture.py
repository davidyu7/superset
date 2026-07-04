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
  2. Summarizes the final decision in SIP terms.
  3. Writes an ``architectural-decision`` knowledge note via the REST API
     so future triage sessions can find precedent.
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys
from typing import Any

import yaml
from devin_client import DevinClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _build_capture_prompt(
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_body: str,
) -> str:
    return f"""\
Capture the architectural decision from the discussion on issue #{issue_number}
in {repo}.

## Issue
**Title:** {issue_title}
**Body:**
{issue_body}

## Instructions
1. Read the full discussion thread on issue #{issue_number}.
2. Summarize the final decision using SIP vocabulary:
   - **Motivation**: what problem was discussed
   - **Proposed Change**: what was agreed upon
   - **New or Changed Public Interfaces**: any API changes
   - **New dependencies**: any new packages
   - **Migration Plan and Compatibility**: upgrade path
   - **Rejected Alternatives**: what was considered and rejected

3. Create an ``architectural-decision`` knowledge note via the Devin
   knowledge API with:
   - ``name``: "architectural-decision:{repo}#{issue_number}: {{short_title}}"
   - ``trigger``: "Precedent for issues related to: {{topic_keywords}}"
   - ``pinned_repo``: "{repo}"
   - ``body``: The SIP-structured summary

4. The note should be findable by future triage sessions searching for
   ``architectural-decision`` in the knowledge base.
"""


def run_capture(
    repo: str,
    issue_number: int,
    issue_title: str,
    issue_body: str,
) -> None:
    """Start a session to capture the architectural decision."""
    config = load_config()
    client = DevinClient()

    max_acu: int = config.get("max_acu_limit", 10)

    prompt = _build_capture_prompt(repo, issue_number, issue_title, issue_body)

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

    if not repo:
        logger.error("GITHUB_REPOSITORY is required")
        sys.exit(1)

    issue_number = int(issue_number_str)
    if issue_number <= 0:
        logger.error("ISSUE_NUMBER must be a positive integer")
        sys.exit(1)

    run_capture(repo, issue_number, issue_title, issue_body)


if __name__ == "__main__":
    main()
