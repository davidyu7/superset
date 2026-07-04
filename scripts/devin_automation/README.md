<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# Devin Automation for Apache Superset

Automated issue triage, remediation, and architectural-decision capture
powered by the [Devin v3 Organization API](https://docs.devin.ai/api-reference/overview).

For full API details and design rationale see
[`DEVIN_API_FINDINGS.md`](DEVIN_API_FINDINGS.md).

---

## Architecture overview

```
GitHub Issue event
  │
  ▼
devin-issue-triage.yml  ─► triage.py
  │                          │
  │  structured_output       │  Devin scoping session
  │  verdict                 │  (AGENTS.md + SECURITY.md constraints)
  │                          │
  ├─ autonomous ────────────►│  implementation session → PR
  │                          │
  └─ needs_discussion ──────►│  SIP-structured comment
                             │  + devin:needs-discussion label

@devin-bot comment
  │
  ▼
devin-feedback.yml  ─► policy.py
  │                      │
  │                      │  READ-MODIFY-WRITE
  │                      │  triage-policy knowledge note
  │                      ▼
  │                   Devin Knowledge API

Issue closed (devin:needs-discussion)
  │
  ▼
devin-decision-capture.yml  ─► decision_capture.py
  │                              │
  │                              │  Devin session summarizes
  │                              │  discussion → architectural-decision
  │                              │  knowledge note
  │                              ▼
  │                           Devin Knowledge API

Weekly (Schedules API)
  │
  ▼
schedules.py  ─► Devin Schedules REST API
                   │
                   │  recurring session
                   │  → Slack + devin-report issue
```

## Secrets required

Only two org-scoped secrets are needed (set as GitHub repo secrets):

| Secret | Description |
|---|---|
| `DEVIN_API_KEY` | `cog_`-prefixed service-user bearer token |
| `DEVIN_ORG_ID` | Devin organization ID |

These are the **only** things to reconfigure when porting to another repo.

## Porting to another repo (e.g. `apache/superset`)

1. **Copy files:**
   ```
   .github/workflows/devin-issue-triage.yml
   .github/workflows/devin-feedback.yml
   .github/workflows/devin-decision-capture.yml
   scripts/devin_automation/*
   ```

2. **Set secrets:** Add `DEVIN_API_KEY` and `DEVIN_ORG_ID` as repo secrets.

3. **Adjust config:** Edit `scripts/devin_automation/config.yaml`:
   - `maintainer_team` — e.g. `apache/superset-committers`
   - `slack_channel_id` — for Slack delivery (optional)
   - `playbook_id` — if you have a triage playbook (optional)
   - `confidence_threshold` — tune autonomous vs. discussion split

4. **Bootstrap:**
   ```bash
   export DEVIN_API_KEY=cog_...
   export DEVIN_ORG_ID=org-...
   export GITHUB_REPOSITORY=apache/superset
   python scripts/devin_automation/bootstrap.py
   ```

No repo names are hardcoded — the target repo is derived from
`${{ github.repository }}` in workflows (matching the
`process.env.GITHUB_REPOSITORY.split('/')` pattern in `supersetbot.yml`).

## Why NOT native GitHub Automation triggers

GitHub Automation triggers in Devin require **private repos**
([DEVIN_API_FINDINGS.md §2.3](DEVIN_API_FINDINGS.md#23-trigger-configuration-details--limitations)).
Since this is a public repo, the trigger layer uses committed GitHub Actions
that call the Sessions API directly.

## Why Sessions/Schedules/Knowledge REST APIs (not UI Automations)

Devin Automations have **no public REST API for creation** — they are
provisioned via the web UI or MCP tool and cannot be committed as code
([DEVIN_API_FINDINGS.md §2.4, §5.2](DEVIN_API_FINDINGS.md#24-how-automations-are-created--managed-important-limitation)).
To keep the system portable and version-controlled, the core loop uses:

- **Sessions API** (`POST /v3/organizations/{org_id}/sessions`) for triage
  and implementation sessions
- **Knowledge API** (`GET/POST/PUT/DELETE .../knowledge/notes`) for the
  feedback loop and architectural-decision notes
- **Schedules API** (`POST/PATCH .../schedules`) for the weekly report

## Label taxonomy

| Label | Meaning |
|---|---|
| `devin:needs-discussion` | Issue requires maintainer discussion before fix |
| `devin:pr-opened` | Devin opened a PR for this issue |
| `devin:triage-in-progress` | Triage session is running |
| `devin-report` | Weekly automation report issue |

## `@devin-bot` directive syntax

Maintainers can steer triage policy by commenting on any issue or PR:

```
@devin-bot always-autonomous: SQL Lab query timeout errors
@devin-bot always-discuss: database migration changes
```

These directives are persisted as `triage-policy` knowledge notes and
consulted by future triage sessions.

## Knowledge note categories

### `triage-policy`
- **Name format:** `triage-policy:<owner/repo>`
- **Trigger:** "Triage policy rules for <repo>"
- **Body:** JSON array of `{ "type": "always-autonomous"|"always-discuss", "pattern": "..." }`
- **Managed by:** `policy.py` via READ-MODIFY-WRITE (PUT is full-replace per
  [DEVIN_API_FINDINGS.md §1.2](DEVIN_API_FINDINGS.md#12-requestresponse-schema-v3-create--update-body))

### `architectural-decision`
- **Name format:** `architectural-decision:<owner/repo>#<issue>: <title>`
- **Trigger:** "Precedent for issues related to: <keywords>"
- **Body:** SIP-structured summary (Motivation / Proposed Change / etc.)
- **Created by:** `decision_capture.py` via Devin session

Both categories use `pinned_repo` for scoping and deterministic name prefixes
for `search` filtering (substring match, not semantic —
[§1.3](DEVIN_API_FINDINGS.md#13-search)).

## AGENTS.md / SECURITY.md / SIP injection

Every session prompt includes:
- Instructions to read `AGENTS.md` (pre-commit, refactor rules, testing)
- Instructions to read `SECURITY.md` (trust boundaries, vulnerability scope)
- The SIP vocabulary from `.github/ISSUE_TEMPLATE/sip.md` for discussion items
- Security triage gate: findings without a named SECURITY.md row + principal
  are routed to `needs_discussion` (per AGENTS.md automated-tooling rule)

Files are referenced by repo-relative path so they resolve in any fork.
