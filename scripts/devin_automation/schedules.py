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
"""Weekly report schedule management via the Schedules REST API.

Idempotent: list existing schedules, create if missing, PATCH if changed.
Preferred over a UI Automation because it is code-committed and portable
(DEVIN_API_FINDINGS.md §2.5, §5.2).
"""

from __future__ import annotations

import logging
import pathlib
import sys
from typing import Any

import yaml
from devin_client import DevinClient, DevinClientError

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _build_report_prompt(repo: str) -> str:
    return f"""\
Generate the weekly Devin automation report for {repo}.

Summarize:
1. **Autonomous fixes**: list all PRs opened by Devin sessions this week,
   with issue number, PR link, and status.
2. **Discussion items**: list all issues labeled `devin:needs-discussion`
   that are still open, framed in SIP vocabulary (Motivation / Proposed Change).
3. **Triage statistics**: total issues triaged, autonomous vs. needs-discussion
   ratio, average confidence.

Post the report as a new issue in {repo} with the label `devin-report`.
Include a summary suitable for Slack delivery.
"""


def ensure_schedule(repo: str) -> None:
    """Create or update the weekly report schedule idempotently."""
    config = load_config()
    client = DevinClient()

    schedule_name: str = config.get("report_schedule_name", "superset-weekly-report")
    rrule: str = config.get(
        "report_schedule_rrule",
        "FREQ=WEEKLY;BYDAY=MO;BYHOUR=9;BYMINUTE=0;BYSECOND=0",
    )
    slack_channel_id: str = config.get("slack_channel_id", "")
    labels = config.get("labels", {})
    report_label: str = labels.get("report", "devin-report")

    prompt = _build_report_prompt(repo)

    try:
        existing = client.list_schedules()
    except DevinClientError:
        logger.warning("Could not list schedules; attempting create")
        existing = []

    match = None
    for sched in existing:
        if sched.get("name") == schedule_name:
            match = sched
            break

    desired: dict[str, Any] = {
        "name": schedule_name,
        "prompt": prompt,
        "schedule_type": "recurring",
        "frequency": rrule,
        "notify_on": "failure",
        "tags": [report_label],
    }
    if slack_channel_id:
        desired["slack_channel_id"] = slack_channel_id

    if match:
        schedule_id = match["schedule_id"]
        needs_update = False
        for key, value in desired.items():
            if match.get(key) != value:
                needs_update = True
                break

        if needs_update:
            client.update_schedule(schedule_id, updates=desired)
            logger.info("Updated schedule %s", schedule_id)
        else:
            logger.info("Schedule %s is up to date", schedule_id)
    else:
        result = client.create_schedule(**desired)
        logger.info("Created schedule: %s", result.get("schedule_id", "unknown"))


def main() -> None:
    import os

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        logger.error("GITHUB_REPOSITORY is required")
        sys.exit(1)

    ensure_schedule(repo)


if __name__ == "__main__":
    main()
