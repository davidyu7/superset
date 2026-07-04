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
"""Maintainer feedback loop -- ``@devin-bot`` directive processing.

Called by ``.github/workflows/devin-feedback.yml``.

Parses directives from issue/PR comments:
  ``@devin-bot always-autonomous: <pattern>``
  ``@devin-bot always-discuss: <pattern>``

Persists them as ``triage-policy`` knowledge notes via READ-MODIFY-WRITE
(PUT is full-replace per DEVIN_API_FINDINGS.md §1.2).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import sys
from typing import Any

import yaml
from devin_client import DevinClient, DevinClientError

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"

DIRECTIVE_RE = re.compile(
    r"@devin-bot\s+(always-autonomous|always-discuss):\s*(.+)",
    re.IGNORECASE,
)


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def parse_directives(comment_body: str) -> list[dict[str, str]]:
    """Extract ``@devin-bot`` directives from a comment."""
    directives: list[dict[str, str]] = []
    for match in DIRECTIVE_RE.finditer(comment_body):
        directive_type = match.group(1).strip().lower()
        pattern = match.group(2).strip()
        directives.append({"type": directive_type, "pattern": pattern})
    return directives


def _note_name(repo: str) -> str:
    return f"triage-policy:{repo}"


def _note_trigger(repo: str) -> str:
    return (
        f"Triage policy rules for {repo}. "
        "Surface when triaging issues in this repository."
    )


def _read_existing_rules(
    client: DevinClient, repo: str
) -> tuple[str | None, str | None, list[dict[str, str]]]:
    """Fetch the existing triage-policy note for this repo.

    Returns (note_id_or_None, folder_id_or_None, existing_rules_list).
    """
    try:
        notes = client.list_knowledge_notes(
            search="triage-policy",
            pinned_repo=repo,
        )
    except DevinClientError:
        logger.warning("Could not fetch existing triage-policy notes")
        return None, None, []

    name = _note_name(repo)
    for note in notes:
        if note.get("name") == name:
            body = note.get("body", "")
            try:
                rules: list[dict[str, str]] = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                rules = []
            return note.get("note_id"), note.get("folder_id"), rules

    return None, None, []


def _merge_rules(
    existing: list[dict[str, str]],
    new_directives: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Merge new directives into existing rules, deduplicating by pattern."""
    by_pattern: dict[str, dict[str, str]] = {}
    for rule in existing:
        if "pattern" in rule:
            by_pattern[rule["pattern"]] = rule
    for directive in new_directives:
        by_pattern[directive["pattern"]] = directive
    return list(by_pattern.values())


def update_policy(
    repo: str,
    directives: list[dict[str, str]],
) -> None:
    """READ-MODIFY-WRITE the triage-policy knowledge note."""
    config = load_config()
    client = DevinClient()

    folder_name: str = config.get("knowledge_folders", {}).get(
        "triage_policy", "devin-triage-policy"
    )

    note_id, existing_folder_id, existing_rules = _read_existing_rules(client, repo)
    merged = _merge_rules(existing_rules, directives)
    body = json.dumps(merged, indent=2)

    name = _note_name(repo)
    trigger = _note_trigger(repo)

    if note_id:
        client.update_knowledge_note(
            note_id,
            name=name,
            body=body,
            trigger=trigger,
            pinned_repo=repo,
            folder_id=existing_folder_id,
        )
        logger.info("Updated triage-policy note %s with %d rules", note_id, len(merged))
    else:
        # Attempt to find the folder ID
        folder_id = _resolve_folder_id(client, folder_name)
        client.create_knowledge_note(
            name=name,
            body=body,
            trigger=trigger,
            pinned_repo=repo,
            folder_id=folder_id,
        )
        logger.info("Created triage-policy note with %d rules", len(merged))


def _resolve_folder_id(client: DevinClient, folder_name: str) -> str | None:
    """Look up a folder ID by name; returns None if not found."""
    try:
        folders = client.list_knowledge_folders()
        for folder in folders:
            if folder.get("name") == folder_name:
                return folder.get("folder_id")
    except DevinClientError:
        logger.warning("Could not list knowledge folders")
    return None


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    comment_body = os.environ.get("COMMENT_BODY", "")

    if not repo:
        logger.error("GITHUB_REPOSITORY is required")
        sys.exit(1)

    directives = parse_directives(comment_body)
    if not directives:
        logger.info("No @devin-bot directives found in comment")
        return

    logger.info("Found %d directive(s): %s", len(directives), directives)
    update_policy(repo, directives)


if __name__ == "__main__":
    main()
