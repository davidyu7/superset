# Devin API / Automation Findings

Research report evaluating the current Devin API and product docs against the planned
GitHub-Actions-based automation for `davidyu7/superset`. Every claim is cited with a
`docs.devin.ai` URL. No `davidyu7/superset` code was modified while producing this report.

**Docs entry point:** <https://docs.devin.ai/api-reference/overview>

## 0. API scopes & authentication (context for everything below)

- The API has two scopes, both on `https://api.devin.ai`:
  - **Organization API** — `https://api.devin.ai/v3/organizations/{org_id}/*` (sessions, knowledge, playbooks, secrets, schedules). "This is where most integrations start."
  - **Enterprise API** — `https://api.devin.ai/v3/enterprise/*` (cross-org: analytics, audit logs, user management).
  - Source: <https://docs.devin.ai/api-reference/overview>
- **Auth:** v3 uses **service users** with role-based access control; tokens are `cog_`-prefixed bearer tokens. Sessions can be attributed to a human via the `create_as_user_id` body param (service user role must include `ImpersonateOrgSessions`). Personal Access Tokens are marked "coming soon."
  Source: <https://docs.devin.ai/api-reference/overview>
- **Legacy v1/v2 APIs** "continue to work during the deprecation period but do not receive new features." Prefer v3.
  Source: <https://docs.devin.ai/api-reference/overview>
- v3 endpoint groups (Sessions, Knowledge notes, Playbooks, Secrets, Schedules, etc.) were promoted from `v3beta1` to `v3` (prod) in Feb 2026 — use `/v3/` base URLs.
  Source: <https://docs.devin.ai/api-reference/release-notes#february-2026>

---

## 1. Knowledge notes — programmatic CRUD, search, scoping

**Verdict: Fully programmable via REST (create, read/list, update, delete, folder tree, substring search). Both proposed note categories can be fully automated.**

### 1.1 Endpoints

Org-scoped (recommended for a single-repo project like superset):

| Operation | Method & path | Doc |
| --- | --- | --- |
| List / search notes | `GET /v3/organizations/{org_id}/knowledge/notes` | <https://docs.devin.ai/api-reference/v3/notes/organizations-knowledge-notes> |
| Create note | `POST /v3/organizations/{org_id}/knowledge/notes` | <https://docs.devin.ai/api-reference/v3/notes/post-organizations-knowledge-notes> |
| Update note (full replace) | `PUT /v3/organizations/{org_id}/knowledge/notes/{note_id}` | (see migration guide row) |
| Delete note | `DELETE /v3/organizations/{org_id}/knowledge/notes/{note_id}` | <https://docs.devin.ai/api-reference/v3/notes/delete-organizations-knowledge-notes-note-id> |
| Folder tree + note counts | `GET /v3/organizations/{org_id}/knowledge/folders` | <https://docs.devin.ai/api-reference/v3/notes/organizations-knowledge-folders> |

Enterprise-scoped equivalents also exist (create/list/update/delete/folders), e.g.
`POST /v3/enterprise/knowledge/notes`, `GET /v3/enterprise/knowledge/notes`,
`PUT|DELETE /v3/enterprise/knowledge/notes/{note_id}`.
Sources: <https://docs.devin.ai/api-reference/v3/notes/post-enterprise-knowledge-notes>,
<https://docs.devin.ai/api-reference/v3/notes/enterprise-knowledge-notes>,
<https://docs.devin.ai/api-reference/v3/notes/put-enterprise-knowledge-notes-note-id>,
<https://docs.devin.ai/api-reference/v3/notes/delete-enterprise-knowledge-notes-note-id>

Migration map (v1 → v3):
Source: <https://docs.devin.ai/api-reference/getting-started/migration-guide#knowledge-endpoints>

```
List   GET    /v1/knowledge              -> GET    /v3/organizations/{org_id}/knowledge/notes
Create POST   /v1/knowledge              -> POST   /v3/organizations/{org_id}/knowledge/notes
Update PUT    /v1/knowledge/{note_id}    -> PUT    /v3/organizations/{org_id}/knowledge/notes/{note_id}
Delete DELETE /v1/knowledge/{note_id}    -> DELETE /v3/organizations/{org_id}/knowledge/notes/{note_id}
```

There is a copy-paste delete example in the docs:
Source: <https://docs.devin.ai/api-reference/common-flows#delete-a-knowledge-note>

```bash
curl -X DELETE "https://api.devin.ai/v3/organizations/$DEVIN_ORG_ID/knowledge/notes/$NOTE_ID" \
  -H "Authorization: Bearer $DEVIN_API_KEY"
```

> Note: There is no separate "get single note by id" REST endpoint documented in v3; retrieve a
> specific note via the **list** endpoint with the `search` / `folder_path` / `pinned_repo` filters
> (the `devin_knowledge_manage` MCP tool exposes a `get` action that resolves the same way).

### 1.2 Request/response schema (v3 create & update body)

Source: <https://docs.devin.ai/api-reference/v3/notes/post-organizations-knowledge-notes>,
<https://docs.devin.ai/api-reference/v3/notes/put-enterprise-knowledge-notes-note-id>

Request body:
- `body: string` (required) — the note content
- `name: string` (required) — short title
- `trigger: string` (required) — natural-language description of *when* the note should surface to Devin
- `folder_id: string | null`
- `is_enabled: boolean | null`
- `pinned_repo: string | null`

Response object (`KnowledgeNoteResponse`):
- `access_type: enum` = `enterprise | org`  ← the scoping field
- `body`, `name`, `trigger`
- `note_id` (prefix `note-`), `org_id`
- `folder_id`, `folder_path`
- `is_enabled: boolean`
- `macro: string | null`
- `pinned_repo: string | null`
- `created_at`, `updated_at` (integer epoch)

> **Full-replace semantics:** `PUT` replaces the whole note. To change one field, `GET`/list the note
> first, then send back all fields. (Confirmed by the built-in `managing-knowledge` skill: "`update`
> uses full-replace PUT semantics.")

### 1.3 Search

The **list** endpoint accepts query params: `after`, `first` (default 100), `access_type`,
`search`, `folder_path`, `pinned_repo`. `search` is a case-insensitive substring match across
name, trigger, and content.
Source: <https://docs.devin.ai/api-reference/v3/notes/enterprise-knowledge-notes> (query params);
`managing-knowledge` skill ("`search` (case-insensitive substring match across name, trigger, and content)").

> Caveat: this is substring filtering, **not** semantic/vector search. Category-tagging in the `name`
> or `trigger` (e.g. name prefixed `triage-policy:` / `architectural-decision:`) is the reliable way
> to enumerate a category via `?search=`.

### 1.4 Scoping

Notes are scoped at two levels via `access_type` (`org` or `enterprise`). There is **no session-level
knowledge note** and **no repository-owned** note type. Repository relevance is expressed two ways:
- `pinned_repo` on the note (associates a note with a repo; also filterable in list).
- `folder_id` / `folder_path` for organizing notes into folders.

Notes surface automatically based on their `trigger` text; additionally a specific note can be forced
into a session at creation time via the `knowledge_ids` array on the session-create call (see §4).
Source (session `knowledge_ids`): <https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions>

### 1.5 Can `triage-policy` and `architectural-decision` be fully automated?

**Yes.** Both are ordinary knowledge notes:
- **Create** on demand (`POST …/knowledge/notes`) with `name`/`trigger` encoding the category.
- **Read/enumerate** with `GET …/knowledge/notes?search=triage-policy` (or `?pinned_repo=`, `?folder_path=`).
- **Update** with `PUT` (remember full-replace — read then write).
- **Delete/dedupe** with `DELETE`.
- Organize each category into its own folder via `folder_id`.

Limitations to design around: (a) full-replace updates (implement read-modify-write); (b) substring
(not semantic) search — rely on deterministic name/trigger prefixes; (c) note creation from the web
UI may require user approval, but **direct API creation with a service user does not** require UI
approval; (d) no repo-owned scope — use `pinned_repo` + folders instead.

---

## 2. Native event automation / triggers (can replace the planned GitHub Actions?)

**Verdict: Yes for the event/trigger layer. Devin's native "Automations" cover schedule (cron), GitHub
repo-activity, Slack, Linear, and generic webhook triggers — directly replacing
`devin-issue-triage.yml`, `devin-feedback.yml`, and `devin-weekly-report.yml`. The main caveat:
Automations are configured in the web app / via the MCP management tool; there is no public REST API
to create an Automation (schedules do have a REST API).**

### 2.1 Automations overview

Automations wire external events → auto-started Devin sessions. An automation = **trigger(s)** +
optional **conditions** + **action**. Multiple triggers on one automation act as an OR.
Source: <https://docs.devin.ai/product-guides/automations>

**Trigger sources & event types** (Source: same page, "Trigger sources" table):

| Source | Event types |
| --- | --- |
| Slack | New message, reaction added |
| GitHub | Issue comment, PR opened/updated, PR review, PR review comment, check run (CI), push |
| Linear | Issue created, label added, status changed, priority changed, assigned |
| Schedule | Recurring (cron / RRULE-based) |
| Webhook | Incoming HTTP POST (any external system: PagerDuty, Datadog, Sentry, custom) |

**Action types** (Source: same page, "Action types" table):
- **Start session** — new session per event; the event payload is auto-appended to the prompt.
- **Message session** — feed the event into an existing long-running session (maintains state).
- **Triage Devin** — persistent Slack-channel monitor that spawns child sub-devins.
- **Email notification** — always / on failure / on success.

### 2.2 Mapping the planned workflows

- **`devin-issue-triage.yml`** → GitHub **Issue comment** trigger (commonly with a `starts_with "/devin"`
  condition) or a **Linear/Jira** issue trigger. There is a built-in template "**/devin Issue Fix** —
  Responds to `/devin` comments on GitHub issues with a fix PR."
  Source: <https://docs.devin.ai/product-guides/automations#configuring-triggers> and templates table.
- **`devin-feedback.yml`** → GitHub **PR review** / **PR review comment** / **issue comment** triggers
  feeding a Start-session or Message-session action. (Note: Devin also *automatically* responds to PR
  comments on non-archived sessions — see §3.)
- **`devin-weekly-report.yml`** → **Schedule** trigger (recurring). Built-in templates include "Weekly
  Changelog", "Weekly Dependency Updates", "Weekly Status Digest to Notion".
  Source: <https://docs.devin.ai/product-guides/automations#templates>

### 2.3 Trigger configuration details & limitations

- **GitHub triggers require selecting a specific repository**, and **only work with private
  repositories** ("GitHub automations only work with private repositories for security reasons").
  ⚠️ This is a hard blocker if the target `superset` repo is public.
  Source: <https://docs.devin.ai/product-guides/automations#github-triggers>
- **Slack triggers** require Devin to be invited to the channel and your personal Slack account linked.
  Source: <https://docs.devin.ai/product-guides/automations#slack-triggers>
- **Schedule triggers** use iCalendar **RRULE** format; times shown local, stored UTC. Sub-hourly
  schedules require a Teams plan or above (per the `managing-automations` skill).
  Source: <https://docs.devin.ai/product-guides/automations#schedule-triggers>
- **Webhook triggers**: after saving you copy a unique HTTPS URL + secret; optional regex payload
  filter; payload is included in the prompt and **truncated above 200 KB**.
  Source: <https://docs.devin.ai/product-guides/automations#webhook-triggers>
- **Safeguards:** per-session **ACU limit**, **invocation limit** (cap firings per window), and an
  optional **network policy** allowlist (important when processing untrusted webhook/Slack input).
  Source: <https://docs.devin.ai/product-guides/automations#limits-and-safeguards>
- **Conditions** are an OR-of-AND-groups filter (e.g. `conclusion = failure` for CI, `starts_with "/devin"`).
  Source: <https://docs.devin.ai/product-guides/automations#core-concepts>
- **MCP integrations** can be attached so triggered sessions can pull logs/metrics (Sentry, Datadog…).
  Source: <https://docs.devin.ai/product-guides/automations#mcp-integrations>

### 2.4 How Automations are created / managed (important limitation)

- **Web app:** Automations page → Create automation (form, template gallery, or natural-language chat).
  Source: <https://docs.devin.ai/product-guides/automations#creating-an-automation>
- **Programmatic management:** via the Devin **MCP** tool `devin_automation_manage`
  (`list | get | schemas | create | update | delete`) — this is how an agent/skill manages them
  (built-in `managing-automations` skill). Updates use PATCH semantics.
- **No documented public REST endpoint** for creating/editing Automations was found in the API
  reference. So Automations themselves are provisioned once (UI/MCP), not as versioned repo code.

### 2.5 Scheduled Sessions (the one trigger type WITH a REST API)

Recurring/one-time scheduled sessions have a full org-scoped REST API — useful for the weekly report if
you want it defined as code rather than an Automation:
Source: <https://docs.devin.ai/api-reference/v3/schedules/post-organizations-schedules>

- `POST /v3/organizations/{org_id}/schedules` — body: `name`*, `prompt`*, `agent` (`devin|data_analyst`),
  `schedule_type` (`recurring|one_time`), `frequency`, `interval_count`, `scheduled_at`,
  `playbook_id`, `notify_on` (`always|failure|never`), `slack_channel_id`, `tags`, `bypass_approval`,
  `target_devin_id`, `create_as_user_id`, `platform`.
- Full CRUD exists: `GET/PATCH/DELETE /v3/organizations/{org_id}/schedules/{schedule_id}`.
  Source (added Feb 2026): <https://docs.devin.ai/api-reference/release-notes#february-2026>
- **Docs recommend Automations over Scheduled Sessions for new schedule work** (Automations add
  event triggers, conditions, invocation limits). Scheduled Sessions still work.
  Source: <https://docs.devin.ai/product-guides/scheduled-sessions#scheduled-sessions>

---

## 3. Native integrations (can they produce PRs / issues / reports / status without Octokit?)

**Verdict: Yes for the common outputs. The GitHub integration lets Devin open PRs, push branches, open
issues, set commit statuses/checks, and respond to PR comments natively — no custom Octokit needed.
Slack, Jira, and Linear cover status updates and ticket→PR flows natively.**

### 3.1 GitHub

- Integrating GitHub "enables Devin to **create pull requests, respond to PR comments, and collaborate
  directly** within your repositories … function as a full contributor."
  Source: <https://docs.devin.ai/integrations/gh#why-integrate-devin-with-github>
- Granted permissions include **read+write** on `pull requests` (create PRs), `contents` (push code),
  `issues` (open issues), `checks` + `commit statuses` (report/ set CI status), `workflows`,
  `discussions`, `projects`.
  Source: <https://docs.devin.ai/integrations/gh#managing-devins-permissions-in-github>
- **Devin automatically responds to PR comments** as long as the session is not archived — this alone
  covers much of the `devin-feedback.yml` intent without any Actions glue.
  Source: <https://docs.devin.ai/integrations/gh#using-devin-with-the-github-integration>
- PR descriptions follow a repo template; Devin also supports a dedicated `devin_pr_template.md`.
  Source: <https://docs.devin.ai/integrations/gh#pull-request-templates>
- **Devin Review** can post findings back to GitHub as **PR comments** and **commit status checks**
  (bugs / security / investigate / note), configurable in Settings → Review.
  Source: <https://docs.devin.ai/work-with-devin/devin-review#posting-to-github>
- ⚠️ Constraints: Devin **cannot create new repositories**; org-level (not per-user) permissions apply;
  GitHub *Automation triggers* require **private** repos (§2.3).
  Source: <https://docs.devin.ai/integrations/gh#security-considerations>

### 3.2 Slack

- Tag `@Devin` in any channel to start a session; Devin replies in-thread with updates/questions.
  Bidirectional Slack thread sync is available. Good for **status updates / reports delivered to Slack**.
  Source: <https://docs.devin.ai/integrations/slack>
- Automations can **deliver scheduled output to a Slack channel** (set `slack_channel_id`,
  `slack_thread_mode: "forward"`), per the `managing-automations` skill and the schedule API's
  `slack_channel_id` field.

### 3.3 Jira

- Assign a ticket to Devin / add `devin` (or playbook) label / `@Devin` comment → Devin starts a session.
  Source: <https://docs.devin.ai/integrations/jira#how-to-trigger-devin-from-jira>
- Devin communicates back in Jira: **PR links auto-added as a remote link + comment**, session link,
  follow-up via `@Devin`.
  Source: <https://docs.devin.ai/integrations/jira#interacting-with-devin-in-jira>
- **"Scoping only" mode**: Devin posts a scoping comment (summary + implementation plan + confidence
  estimate) instead of doing the work — directly relevant to a triage/scoping step (§4.5).
  Source: <https://docs.devin.ai/integrations/jira#session-mode>
- Optional OAuth **service account** so comments appear as a bot, not a person.
  Source: <https://docs.devin.ai/integrations/jira#connecting-a-service-account>

### 3.4 Linear

- Assign to Devin / playbook label / `@Devin` comment → session. Native Linear tools come with the
  integration (no separate MCP).
  Source: <https://docs.devin.ai/integrations/linear>
- Communicates via Linear's **agent session** UI: real-time activity feed, **plan/todo sync**,
  **PR links auto-added**, session link, stop signal.
  Source: <https://docs.devin.ai/integrations/linear#interacting-with-devin-in-linear>

### 3.5 Native "automation triggers" inside Jira/Linear

Both Jira and Linear have their own **Automation triggers** (projects/teams, labels, statuses,
optional playbook) using **edge detection** (fire only on transition into a matching state) — an
alternative to GitHub-side triggering for ticket-driven work.
Sources: <https://docs.devin.ai/integrations/jira#automation-triggers>,
<https://docs.devin.ai/integrations/linear#automation-triggers>

---

## 4. Session lifecycle (endpoints, status fields, structured output, PR/blocked detection)

**Verdict: Confirmed. Prefer v3. `status` + `status_detail` (v3) reliably distinguish working /
waiting-for-user / finished; `pull_requests[]` gives PR detection; a JSON verdict is returned via
`structured_output` with an enforceable `structured_output_schema`.**

### 4.1 Endpoints — the task referenced v1; here is the current mapping

Source: <https://docs.devin.ai/api-reference/getting-started/migration-guide#session-endpoints>

```
Create   POST /v1/sessions                     -> POST /v3/organizations/{org_id}/sessions
List     GET  /v1/sessions                     -> GET  /v3/organizations/{org_id}/sessions
Get      GET  /v1/session/{session_id}         -> GET  /v3/organizations/{org_id}/sessions/{devin_id}
Message  POST /v1/session/{session_id}/message -> POST /v3/organizations/{org_id}/sessions/{devin_id}/messages
```

Doc links:
- Create: <https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions>
- Get: <https://docs.devin.ai/api-reference/v3/sessions/get-organizations-session>
- Message: <https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions-messages>
- List messages: <https://docs.devin.ai/api-reference/v3/sessions/get-organizations-session-messages>

> ⚠️ Path/naming corrections vs. the planned design:
> - v1 message path was `POST /v1/sessions/{session_id}/message` (singular `message`); the current v3
>   path is `.../messages` (plural). Source:
>   <https://docs.devin.ai/api-reference/v1/sessions/send-a-message-to-an-existing-devin-session>
> - v1 send-message returns `{ detail: string }` (null on success / a detail string if already
>   suspended). v3 send-message returns the **full session object**.

### 4.2 Status field — the exact names/values differ between v1 and v3

**v1 `GET /v1/sessions/{session_id}`** returns both `status: string` (freeform) **and**
`status_enum` with enum:
`working, blocked, expired, finished, suspend_requested, suspend_requested_frontend,
resume_requested, resume_requested_frontend, resumed`.
It also returns `messages[]`, `pull_request` (single, `{ url }`), `structured_output`, `title`, `tags`.
Source: <https://docs.devin.ai/api-reference/v1/sessions/retrieve-details-about-an-existing-session>

**v3 `GET /v3/organizations/{org_id}/sessions/{devin_id}`** does **not** use `status_enum`. It returns:
- `status: enum` = `new | claimed | running | exit | error | suspended | resuming`
- `status_detail: enum | null` — the field to key off for lifecycle:
  - When `status = running`: `working` | `waiting_for_user` | `waiting_for_approval` | `finished`
  - When `status = suspended`: reason such as `inactivity`, `user_request`, `usage_limit_exceeded`,
    `out_of_credits`, `total_session_limit_exceeded`, `error`, etc.
- `pull_requests: [{ pr_url, pr_state }]` (array; `pr_state` may be null)
- `structured_output`, `category`/`subcategory`, `origin`, `parent_session_id`, `child_session_ids`,
  `acus_consumed`, `created_at`/`updated_at` (epoch int), `url`, `tags`.
Source: <https://docs.devin.ai/api-reference/v3/sessions/get-organizations-session>

> **Recommendation:** target v3 and read `status` + `status_detail`. Do **not** rely on a v3
> `status_enum` field — it exists only in v1.

### 4.3 Detecting "PR opened" vs "blocked / needs input"

- **PR opened:** poll `GET …/sessions/{devin_id}` and check the `pull_requests` array is non-empty and
  read `pull_requests[].pr_url` / `pr_state`. (v1 exposes a single `pull_request.url`.)
- **Blocked / needs input:** v3 `status = running` with `status_detail = waiting_for_user` (or
  `waiting_for_approval` in safe mode). v1 uses `status_enum = blocked`.
- **Done:** v3 `status_detail = finished` (or `status = exit`); v1 `status_enum = finished`.
- **Failed/exhausted:** v3 `status = error`, or `status = suspended` with a limit/credit
  `status_detail`. Sources: the two GET-session docs cited in §4.1–§4.2.

### 4.4 Sending follow-ups

`POST /v3/organizations/{org_id}/sessions/{devin_id}/messages` with `{ "message": "...",
"attachment_urls": [...] }`; "the session will be automatically resumed if suspended."
Source: <https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions-messages>

### 4.5 Structured JSON verdict for the triage/scoping step — YES

On `POST /v3/organizations/{org_id}/sessions` you can enforce a machine-readable verdict:
Source: <https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions>

- `structured_output_schema: object | null` — a **JSON Schema (Draft 7)** to validate the output.
  "Max 64KB. Must be self-contained (no external `$ref`)."
- `structured_output_required: boolean` (default **true**) — when true the agent **MUST** call
  `provide_structured_output` with `is_final=true` before its turn ends; when false the tool is
  available but not guaranteed to be called.
- The validated result is returned as `structured_output` on the **get/list** session endpoints
  (populated only there — not on the create response).

Other useful create-body params for the triage/scoping session: `repos`, `playbook_id`,
`knowledge_ids` (attach specific notes), `tags`, `max_acu_limit`, `idempotent`-style
`session_secrets`/`secret_ids`, `bypass_approval`, `create_as_user_id`, `title`.

> This means the triage/scoping step can return a strict JSON verdict (e.g.
> `{ "action": "triage" | "needs_info" | "wont_fix", "confidence": 0-1, "labels": [...],
> "rationale": "..." }`) that downstream logic can branch on deterministically — no scraping of
> free-text messages required. (Jira "Scoping only" mode is a UI-native alternative — §3.3.)

---

## 5. Recommendation — revised architecture (maximize native, minimize custom glue)

### 5.1 What native Devin features REPLACE in the planned design

| Previously planned (custom) | Replace with (native Devin) |
| --- | --- |
| `devin-issue-triage.yml` GitHub Action | **Automation**: GitHub *Issue comment* trigger (`starts_with "/devin"`) **or** Linear/Jira issue trigger → *Start session* with a triage **playbook** + `structured_output_schema` verdict. (Template: "/devin Issue Fix".) |
| `devin-feedback.yml` GitHub Action | Mostly **built-in**: Devin auto-responds to PR comments on non-archived sessions. For richer routing, an **Automation** on *PR review* / *PR review comment* / *issue comment* → *Message session*. Optionally **Devin Review** posting findings as PR comments / commit checks. |
| `devin-weekly-report.yml` GitHub Action | **Automation** *Schedule (recurring, RRULE)* → *Start session* with Slack delivery (`slack_channel_id`, `slack_thread_mode:"forward"`); **or** the code-defined **Schedules REST API** (`POST /v3/organizations/{org_id}/schedules`). Template: "Weekly Changelog". |
| Octokit "create PR" / "push branch" / "open issue" scripts | **Native GitHub integration** — Devin opens PRs, pushes branches, opens issues, sets checks/commit status directly. |
| Octokit comment **upsert** for status | **Native**: Devin posts/updates PR + issue comments itself; Jira/Linear auto-post PR links + session links; Slack in-thread updates. |
| Cron runner infra (Actions `schedule:`) | **Automation Schedule trigger** or **Schedules API** (server-side, RRULE). |
| Custom secret plumbing for `GITHUB_TOKEN` in Actions | **GitHub App integration** (org-level permissions) — no PAT juggling. API auth via **service user** (`cog_`). |
| Bespoke "is it done / blocked?" parsing of logs | **`status` + `status_detail`** and **`pull_requests[]`** from `GET …/sessions/{devin_id}`; **`structured_output`** for the verdict. |

### 5.2 What must remain custom (or provisioned once, out-of-band)

- **Label-based state machine** (if you truly need PR/issue *labels* as the source of truth): Devin has
  the `issues`/`pull requests` write scope so a session *can* set labels as an action, but there is no
  first-class "label state" primitive and GitHub *label-changed* is **not** in the Automation trigger
  list (triggers are issue comment / PR / PR review / PR review comment / check run / push). If
  label-driven orchestration is required, keep a thin custom layer (a small Action or webhook →
  Automation *Webhook* trigger). Jira/Linear **do** have native label/status triggers with edge
  detection, so ticket-labels are natively supported there.
- **Automation provisioning as code:** Automations have **no public REST/create API** — they're set up
  in the web UI or via the `devin_automation_manage` MCP tool, not committed to the repo. If you need
  schedule-as-code, use the **Schedules REST API** instead (it is code-manageable). Knowledge notes,
  sessions, and schedules are all REST-manageable; Automations are not.
- **Public-repo GitHub triggers:** GitHub Automation triggers require **private** repos. If `superset`
  is public, keep a minimal GitHub Action (or external webhook) that calls the **Webhook Automation
  trigger** or the **sessions API** directly.
- **Any non-GitHub/Slack/Linear/Jira event source** (e.g. a bespoke internal system): use the
  **Webhook** trigger (200 KB payload cap) or call `POST /v3/organizations/{org_id}/sessions` from your
  own glue.
- **Knowledge-note read-modify-write logic** for the maintainer feedback loop: because `PUT` is
  full-replace and search is substring-only, keep small client logic that (a) lists by a deterministic
  `name`/`trigger` category prefix, (b) merges, (c) re-`PUT`s.

### 5.3 Suggested target design (concise)

1. **Triage** (`triage-policy` feedback loop): GitHub *issue-comment* Automation (or ticket trigger) →
   Start session with a triage playbook + `structured_output_schema` → branch on the JSON verdict; Devin
   opens the PR / posts the triage comment natively; on approval, append a `triage-policy` **knowledge
   note** via the REST API (folder + `pinned_repo=davidyu7/superset`).
2. **Feedback**: rely on native PR-comment responses + optional Devin Review; add a *PR review comment*
   Automation only if you need to fan out to other channels.
3. **Weekly report**: a **Schedule** (Automation or Schedules API) → Start session → deliver to Slack;
   summarize resolved discussions into `architectural-decision` **knowledge notes** via REST.
4. **State/observability**: poll `GET …/sessions/{devin_id}` for `status`/`status_detail`/`pull_requests`;
   use tags for filtering; keep only a thin custom shim for label-state if genuinely required.

---

## Appendix — primary sources

- API overview / scopes / auth: <https://docs.devin.ai/api-reference/overview>
- Migration guide (v1→v3 sessions & knowledge): <https://docs.devin.ai/api-reference/getting-started/migration-guide>
- Release notes (v3 promotion, schedules Feb 2026): <https://docs.devin.ai/api-reference/release-notes>
- Knowledge notes (org): create <https://docs.devin.ai/api-reference/v3/notes/post-organizations-knowledge-notes>,
  list <https://docs.devin.ai/api-reference/v3/notes/organizations-knowledge-notes>,
  delete <https://docs.devin.ai/api-reference/v3/notes/delete-organizations-knowledge-notes-note-id>,
  folders <https://docs.devin.ai/api-reference/v3/notes/organizations-knowledge-folders>
- Knowledge notes (enterprise): <https://docs.devin.ai/api-reference/v3/notes/post-enterprise-knowledge-notes>,
  <https://docs.devin.ai/api-reference/v3/notes/put-enterprise-knowledge-notes-note-id>,
  <https://docs.devin.ai/api-reference/v3/notes/delete-enterprise-knowledge-notes-note-id>
- Sessions: create <https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions>,
  get <https://docs.devin.ai/api-reference/v3/sessions/get-organizations-session>,
  message <https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions-messages>,
  v1 get (status_enum) <https://docs.devin.ai/api-reference/v1/sessions/retrieve-details-about-an-existing-session>
- Automations: <https://docs.devin.ai/product-guides/automations>
- Scheduled sessions: <https://docs.devin.ai/product-guides/scheduled-sessions>;
  Schedules API <https://docs.devin.ai/api-reference/v3/schedules/post-organizations-schedules>
- Integrations: GitHub <https://docs.devin.ai/integrations/gh>, Slack <https://docs.devin.ai/integrations/slack>,
  Jira <https://docs.devin.ai/integrations/jira>, Linear <https://docs.devin.ai/integrations/linear>
- Devin Review posting to GitHub: <https://docs.devin.ai/work-with-devin/devin-review#posting-to-github>
</content>
</invoke>
