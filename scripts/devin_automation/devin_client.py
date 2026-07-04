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
"""Devin v3 Organization API client.

Thin wrapper around the endpoints documented in
``scripts/devin_automation/DEVIN_API_FINDINGS.md``.
Auth: ``cog_``-prefixed service-user bearer token via ``DEVIN_API_KEY`` env var.

All repo references are passed in -- nothing is hardcoded.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.devin.ai/v3/organizations"


class DevinClientError(Exception):
    """Raised when the Devin API returns a non-success status."""


class DevinClient:
    """Stateless helper for the Devin v3 Organization API."""

    def __init__(
        self,
        api_key: str | None = None,
        org_id: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ["DEVIN_API_KEY"]
        self.org_id = org_id or os.environ["DEVIN_ORG_ID"]
        self._base = f"{BASE_URL}/{self.org_id}"

    # ── helpers ──────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        resp = requests.request(
            method,
            url,
            headers=self._headers(),
            json=json_body,
            params=params,
            timeout=60,
        )
        if not resp.ok:
            logger.error("%s %s -> %d: %s", method, url, resp.status_code, resp.text)
            raise DevinClientError(f"{method} {url} -> {resp.status_code}: {resp.text}")
        if resp.status_code == 204:
            return {}
        return resp.json()

    # ── Sessions (§4 of DEVIN_API_FINDINGS.md) ───────────────────────────

    def create_session(
        self,
        prompt: str,
        *,
        repos: list[str] | None = None,
        playbook_id: str | None = None,
        knowledge_ids: list[str] | None = None,
        structured_output_schema: dict[str, Any] | None = None,
        structured_output_required: bool = True,
        tags: list[str] | None = None,
        max_acu_limit: int | None = None,
        title: str | None = None,
        bypass_approval: bool | None = None,
    ) -> dict[str, Any]:
        """POST /v3/organizations/{org_id}/sessions"""
        body: dict[str, Any] = {"prompt": prompt}
        if repos is not None:
            body["repos"] = repos
        if playbook_id:
            body["playbook_id"] = playbook_id
        if knowledge_ids:
            body["knowledge_ids"] = knowledge_ids
        if structured_output_schema is not None:
            body["structured_output_schema"] = structured_output_schema
            body["structured_output_required"] = structured_output_required
        if tags:
            body["tags"] = tags
        if max_acu_limit is not None:
            body["max_acu_limit"] = max_acu_limit
        if title:
            body["title"] = title
        if bypass_approval is not None:
            body["bypass_approval"] = bypass_approval
        return self._request("POST", "/sessions", json_body=body)

    def get_session(self, devin_id: str) -> dict[str, Any]:
        """GET /v3/organizations/{org_id}/sessions/{devin_id}"""
        return self._request("GET", f"/sessions/{devin_id}")

    def send_message(
        self,
        devin_id: str,
        message: str,
        *,
        attachment_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        """POST /v3/organizations/{org_id}/sessions/{devin_id}/messages"""
        body: dict[str, Any] = {"message": message}
        if attachment_urls:
            body["attachment_urls"] = attachment_urls
        return self._request("POST", f"/sessions/{devin_id}/messages", json_body=body)

    def poll_session(
        self,
        devin_id: str,
        *,
        interval: int = 30,
        max_attempts: int = 120,
    ) -> dict[str, Any]:
        """Poll until the session reaches a terminal state.

        Terminal states (v3): status_detail in {finished} or
        status in {exit, error, suspended}.
        """
        for attempt in range(max_attempts):
            session = self.get_session(devin_id)
            status = session.get("status", "")
            detail = session.get("status_detail")

            logger.info(
                "poll %d/%d  status=%s  detail=%s",
                attempt + 1,
                max_attempts,
                status,
                detail,
            )

            if status in ("exit", "error", "suspended"):
                return session
            if status == "running" and detail in (
                "finished",
                "waiting_for_user",
            ):
                return session

            time.sleep(interval)

        return self.get_session(devin_id)

    # ── Knowledge notes (§1 of DEVIN_API_FINDINGS.md) ────────────────────

    def list_knowledge_notes(
        self,
        *,
        search: str | None = None,
        pinned_repo: str | None = None,
        folder_path: str | None = None,
        first: int = 100,
    ) -> list[dict[str, Any]]:
        """GET /v3/organizations/{org_id}/knowledge/notes"""
        params: dict[str, Any] = {"first": first}
        if search:
            params["search"] = search
        if pinned_repo:
            params["pinned_repo"] = pinned_repo
        if folder_path:
            params["folder_path"] = folder_path
        resp = self._request("GET", "/knowledge/notes", params=params)
        return resp.get("items", resp.get("data", []))

    def create_knowledge_note(
        self,
        *,
        name: str,
        body: str,
        trigger: str,
        pinned_repo: str | None = None,
        folder_id: str | None = None,
        is_enabled: bool = True,
    ) -> dict[str, Any]:
        """POST /v3/organizations/{org_id}/knowledge/notes"""
        payload: dict[str, Any] = {
            "name": name,
            "body": body,
            "trigger": trigger,
            "is_enabled": is_enabled,
        }
        if pinned_repo:
            payload["pinned_repo"] = pinned_repo
        if folder_id:
            payload["folder_id"] = folder_id
        return self._request("POST", "/knowledge/notes", json_body=payload)

    def update_knowledge_note(
        self,
        note_id: str,
        *,
        name: str,
        body: str,
        trigger: str,
        pinned_repo: str | None = None,
        folder_id: str | None = None,
        is_enabled: bool = True,
    ) -> dict[str, Any]:
        """PUT /v3/organizations/{org_id}/knowledge/notes/{note_id}

        Full-replace semantics -- all fields must be supplied.
        """
        payload: dict[str, Any] = {
            "name": name,
            "body": body,
            "trigger": trigger,
            "is_enabled": is_enabled,
        }
        if pinned_repo:
            payload["pinned_repo"] = pinned_repo
        if folder_id:
            payload["folder_id"] = folder_id
        return self._request("PUT", f"/knowledge/notes/{note_id}", json_body=payload)

    def delete_knowledge_note(self, note_id: str) -> dict[str, Any]:
        """DELETE /v3/organizations/{org_id}/knowledge/notes/{note_id}"""
        return self._request("DELETE", f"/knowledge/notes/{note_id}")

    def list_knowledge_folders(self) -> list[dict[str, Any]]:
        """GET /v3/organizations/{org_id}/knowledge/folders"""
        resp = self._request("GET", "/knowledge/folders")
        return resp.get("items", resp.get("data", []))

    # ── Schedules (§2.5 of DEVIN_API_FINDINGS.md) ────────────────────────

    def create_schedule(
        self,
        *,
        name: str,
        prompt: str,
        schedule_type: str = "recurring",
        frequency: str | None = None,
        notify_on: str = "failure",
        slack_channel_id: str | None = None,
        tags: list[str] | None = None,
        playbook_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /v3/organizations/{org_id}/schedules"""
        body: dict[str, Any] = {
            "name": name,
            "prompt": prompt,
            "schedule_type": schedule_type,
        }
        if frequency:
            body["frequency"] = frequency
        if notify_on:
            body["notify_on"] = notify_on
        if slack_channel_id:
            body["slack_channel_id"] = slack_channel_id
        if tags:
            body["tags"] = tags
        if playbook_id:
            body["playbook_id"] = playbook_id
        return self._request("POST", "/schedules", json_body=body)

    def list_schedules(self) -> list[dict[str, Any]]:
        """GET /v3/organizations/{org_id}/schedules"""
        resp = self._request("GET", "/schedules")
        return resp.get("items", resp.get("data", []))

    def update_schedule(
        self,
        schedule_id: str,
        *,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """PATCH /v3/organizations/{org_id}/schedules/{schedule_id}"""
        return self._request("PATCH", f"/schedules/{schedule_id}", json_body=updates)

    def delete_schedule(self, schedule_id: str) -> dict[str, Any]:
        """DELETE /v3/organizations/{org_id}/schedules/{schedule_id}"""
        return self._request("DELETE", f"/schedules/{schedule_id}")
