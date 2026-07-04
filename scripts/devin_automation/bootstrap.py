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
"""One-time bootstrap for the Devin automation system.

Run after copying files to a new repo and setting DEVIN_API_KEY + DEVIN_ORG_ID:

    GITHUB_REPOSITORY=owner/repo python scripts/devin_automation/bootstrap.py

Creates:
  - Knowledge-note folders (triage-policy, architectural-decisions)
  - An initial empty triage-policy note
  - The weekly report schedule
"""

from __future__ import annotations

import json
import logging
import os
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


def _ensure_initial_policy_note(
    client: DevinClient, repo: str, folder_name: str
) -> None:
    """Create an initial triage-policy note if none exists."""
    existing = client.list_knowledge_notes(
        search="triage-policy",
        pinned_repo=repo,
    )

    note_name = f"triage-policy:{repo}"
    for note in existing:
        if note.get("name") == note_name:
            logger.info("Triage-policy note already exists: %s", note.get("note_id"))
            return

    folder_id = _resolve_folder_id(client, folder_name)

    client.create_knowledge_note(
        name=note_name,
        body=json.dumps([], indent=2),
        trigger=(
            f"Triage policy rules for {repo}. "
            "Surface when triaging issues in this repository."
        ),
        pinned_repo=repo,
        folder_id=folder_id,
    )
    logger.info("Created initial triage-policy note for %s", repo)


def _resolve_folder_id(client: DevinClient, folder_name: str) -> str | None:
    """Look up a folder by name."""
    try:
        folders = client.list_knowledge_folders()
        for folder in folders:
            if folder.get("name") == folder_name:
                return folder.get("folder_id")
    except DevinClientError:
        logger.warning("Could not list knowledge folders")
    return None


def bootstrap(repo: str) -> None:
    """Run all bootstrap steps."""
    config = load_config()
    client = DevinClient()

    knowledge_folders: dict[str, str] = config.get("knowledge_folders", {})
    triage_folder: str = knowledge_folders.get("triage_policy", "devin-triage-policy")

    # Step 1: Log folder names (folders are created via UI or MCP, not REST)
    logger.info(
        "Ensure the following knowledge-note folders exist in the Devin UI: %s",
        list(knowledge_folders.values()),
    )

    # Step 2: Create initial triage-policy note
    _ensure_initial_policy_note(client, repo, triage_folder)

    # Step 3: Register weekly report schedule
    logger.info("Registering weekly report schedule...")
    from schedules import ensure_schedule

    ensure_schedule(repo)

    logger.info("Bootstrap complete for %s", repo)


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        logger.error(
            "Usage: GITHUB_REPOSITORY=owner/repo python bootstrap.py\n"
            "Also requires DEVIN_API_KEY and DEVIN_ORG_ID env vars."
        )
        sys.exit(1)

    bootstrap(repo)


if __name__ == "__main__":
    main()
