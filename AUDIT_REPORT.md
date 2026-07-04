# Security, Dependency & Performance Audit — `davidyu7/superset`

**Audit date:** 2026-07-04
**Repo:** `davidyu7/superset` (fork of `apache/superset`)
**Commit audited:** `43f2816240` (branch `master`, dated 2026-07-04)
**Scope:** Read-only audit. No code was changed. This report identifies findings only.

> **Threat-model caveat (important).** Superset's `AGENTS.md` / `SECURITY.md` define three
> trust boundaries: the **Admin** role is fully trusted, the **operator** owns deployment-time
> choices (secrets, connectors, feature flags, network exposure), and the **codebase** must
> enforce the role/capability matrix at every entry point. Several static-analysis hits below
> land inside Admin/operator-trusted code paths (extension loading, engine-spec DDL, migrations)
> and are therefore **not vulnerabilities** under that model — they are flagged here as
> "review / defense-in-depth" so a human can confirm the trust assumption, not as confirmed bugs.

---

## 0. Executive Summary — Prioritized Findings

| # | Pri | Area | Finding | Location |
|---|-----|------|---------|----------|
| F1 | **High** | Dependency | `flask==2.3.3` vulnerable to CVE-2026-27205 (`Vary: Cookie` cache leak). Fix: 3.1.3 | `requirements/base.txt` |
| F2 | **High** | Dependency | `paramiko==3.5.1` vulnerable to CVE-2026-44405 (SHA-1 allowed in `rsakey.py`) | `requirements/base.txt` |
| F3 | **Medium** | Perf | Per-statement `db.session.commit()` inside execution loop (metadata-DB commit per SQL statement) | `superset/sql/execution/executor.py:183` |
| F4 | **Medium** | Perf | Per-block `db.session.refresh()` + `commit()` inside SQL Lab block loop | `superset/sql_lab.py:516,529` |
| F5 | **Medium** | Dependency (dev) | Dev-only CVEs: `pip`, `pytest`, `python-multipart`, `jaraco-context` | `requirements/development.txt` |
| F6 | **Medium** | Security review | `exec()` of extension bytecode in `.supx` loader (Admin/operator-trusted) | `superset/extensions/utils.py:64` |
| F7 | **Medium** | Security review | `yaml.load`/unsafe-load path in examples import | `superset/examples/utils.py:261` |
| F8 | **Low/Medium** | Security review | 23 string-built SQL sites (engine specs, migrations, MCP tools) — confirm parameterization | see §4.1 |
| F9 | **Low** | Security review | 7 `markupsafe.Markup(...)` uses on model-derived strings (verify inputs are trusted) | see §4.2 |
| F10 | **Low** | Security review | 5 `md5()` uses flagged as weak hash — all non-security (cache keys / signatures) | see §4.3 |
| F11 | **Low** | Perf/Correctness | Open upstream issues affecting this HEAD (owner-table N+1/dupes, stale export cache, slow SSH tunnel) | §2 |

**Frontend production dependencies: `npm audit --omit=dev` reported 0 vulnerabilities.**

---

## 1. Fork vs. Upstream Divergence

Commands run:

```bash
git remote add upstream https://github.com/apache/superset.git && git fetch upstream
git log --oneline HEAD..upstream/master      # commits upstream has that the fork lacks
git log --oneline upstream/master..HEAD      # commits the fork has that upstream lacks
```

**Result: the fork has ZERO divergence from upstream.**

```
HEAD..upstream/master  -> 0 commits
upstream/master..HEAD  -> 0 commits
```

`HEAD`, `origin/master`, and `upstream/master` all point at the same commit
`43f2816240 chore(deps-dev): update ydb-sqlglot-plugin requirement ... (#41764)`.

**Implication:** There is no fork-specific code, so every finding below originates in upstream
`apache/superset` at this commit, not in fork-introduced changes. No custom/unreviewed patches exist.
(The fork is an exact mirror; `davidyu7/superset` has **Issues disabled**, so all issue analysis in §2
is against `apache/superset`.)

---

## 2. Open Issues & Published Security Advisories

### 2.1 Issues
* `davidyu7/superset`: **Issues are disabled** on the fork (`gh issue list` → "repository has disabled issues").
* `apache/superset`: **866 open issues**. Items most relevant to correctness/performance/security of this HEAD:

| Issue | Title | Relevance |
|-------|-------|-----------|
| [#41623](https://github.com/apache/superset/issues/41623) | Owner association tables lack `UniqueConstraint`, letting duplicate owners break owner removal (422) | Data-integrity / correctness |
| [#41687](https://github.com/apache/superset/issues/41687) | Dashboard/Chart/Dataset export endpoints inherit 1-year `Cache-Control`, causing stale/cached exports | Correctness / cache; security-adjacent (stale data) |
| [#41745](https://github.com/apache/superset/issues/41745) | SSH Tunneling is quite slow | Performance (`#bug:performance` label) |
| [#41610](https://github.com/apache/superset/issues/41610) | MCP JWT auth: `g.user` not found | Auth path (MCP) |
| [#41578](https://github.com/apache/superset/issues/41578) | SSO session not updated after logout/login (Keycloak) | Auth/session |
| [#41280](https://github.com/apache/superset/issues/41280) | Containers crash: `sqlglot.expressions` has no attribute `YearOfWeek` | Stability (sqlglot version) |

### 2.2 Published GitHub Security Advisories (`apache/superset`)
`gh api /advisories?ecosystem=pip&affects=apache-superset` returns **69 published advisories** (all historical
CVEs for the product). The commit audited is post-6.1.0 `master`, so these are **fixed** in this tree; they are
listed as the product's disclosed vulnerability classes to watch when back/forward-porting. Highest-severity examples:

| Severity | ID | Summary |
|----------|-----|---------|
| Critical | GHSA-rwhh-6x83-84v6 / CVE-2023-49657 | Stored XSS |
| Critical | GHSA-wq8q-99p5-xfrw / CVE-2022-27479 | SQL injection |
| High | GHSA-mwf2-qr4v-94h2 / CVE-2026-23984 | Read-only bypass via improper input validation (PostgreSQL) |
| High | GHSA-3m2g-v7jf-7fxc / CVE-2026-23982 | Improper authorization — low-priv access-control bypass |
| High | GHSA-8w7f-8pr9-xgwj / CVE-2025-48912 | RLS authorization bypass via SQL injection |
| High | GHSA-787v-v9vq-4rgv / CVE-2024-55633 | SQLLab read-only validation bypass → unauthorized write |
| Medium | GHSA-fj97-2v9x-w5m4 / CVE-2025-55672 | Stored XSS in chart visualization |

The recurring classes (SQLi via engine specs, RLS/authorization bypass, stored XSS, `DISALLOWED_SQL_FUNCTIONS`
bypass, metadata disclosure) directly motivate the static-review focus in §4.

---

## 3. Dependency Vulnerability Scan

### 3.1 Backend — `pip-audit`

**Production runtime (`requirements/base.txt`) — 2 findings:**

| Package | Installed | ID | Severity | Fix | Notes |
|---------|-----------|-----|----------|-----|-------|
| `flask` | 2.3.3 | CVE-2026-27205 | **High** | **3.1.3** | Missing `Vary: Cookie` when session accessed via `in`; response may be cached by a caching proxy and leak per-user content. `pyproject.toml` allows `flask>=2.2.5,<4.0.0`, so a bump is unblocked. |
| `paramiko` | 3.5.1 | CVE-2026-44405 | **High** | (4.x) | `rsakey.py` still permits SHA-1. Fix is in paramiko 4.x, but `pyproject.toml` pins `paramiko<4.0` (and override `paramiko="3"`) because `sshtunnel` still references `DSSKey`. **Bump blocked** — track `sshtunnel` compatibility before upgrading. Relevant to the SSH-tunnel DB-connection feature. |

**Dev/build chain (`requirements/development.txt`) — additional 10 findings (not shipped to prod):**

| Package | Installed | ID(s) | Fix |
|---------|-----------|-------|-----|
| `pip` | 25.1.1 | PYSEC-2026-196, CVE-2025-8869, CVE-2026-1703, CVE-2026-3219, CVE-2026-6357 | 26.1.2 |
| `pytest` | 7.4.4 | CVE-2025-71176 | 9.0.3 |
| `python-multipart` | 0.0.29 | CVE-2026-53540/53539/53538 | 0.0.31 |
| `jaraco-context` | 6.0.1 | CVE-2026-23949 | 6.1.0 |

> `requirements/development.txt` initially failed pip-audit because `mysqlclient` needs system build deps;
> resolved by installing `pkg-config` + `default-libmysqlclient-dev` and re-running. `apache-superset (0.0.0.dev0)`
> is skipped (local package, not on PyPI).

### 3.2 Frontend — `npm audit`

```bash
cd superset-frontend && npm audit --omit=dev   # (== --production)
# found 0 vulnerabilities
```

**No production frontend advisories.** (Only dev-time tooling could surface issues; production bundle is clean.)

### Recommended version bumps
1. **`flask` → `3.1.3`** (High, unblocked by constraint) — highest-value single change.
2. **`paramiko`**: cannot move to 4.x until `sshtunnel` drops `DSSKey`; monitor and pin-bump within 3.x if a
   patched 3.x is released. Document the residual SHA-1 risk.
3. Dev chain: bump `pip`, `pytest`, `python-multipart`, `jaraco-context` in `requirements/development.*`
   (defense-in-depth for CI/build hosts; not user-facing).

---

## 4. Static Security Review

Tools: `bandit -r superset/` (176k LOC scanned) and
`semgrep --config p/python --config p/security-audit --config p/owasp-top-ten`.
Bandit severity totals: **High 5, Medium 38, Low 204**. Semgrep: **71 findings (20 ERROR / 50 WARNING / 1 INFO)**.

### 4.1 SQL construction (SQLi class) — **Low/Medium, review**
Bandit `B608` (23) + Semgrep `avoid-sqlalchemy-text` (18) / `sql-injection-db-cursor-execute` (2). Concentrations:

* **Engine specs** (dialect DDL/metadata SQL built as strings):
  `superset/db_engine_specs/bigquery.py:1132,1172`, `clickhouse.py:405-406`, `gsheets.py:432`,
  `mssql.py:196`, `postgres.py:872`, `presto.py:520,716`, `redshift.py:375`, `hive.py:237,264`, `odps.py:186`.
* **Migrations / shared utils:** `superset/migrations/shared/utils.py:133,615,690`,
  `superset/migrations/versions/2026-06-02_...add_sessions_invalidated_at.py:85,112-165`,
  `superset/utils/encrypt.py:305,513`, `superset/utils/mock_data.py:291`, `superset/examples/generic_loader.py:89`.
* **MCP tools:** `superset/mcp_service/sql_lab/tool/execute_sql.py:178`,
  `superset/mcp_service/dataset/tool/create_virtual_dataset.py:199`,
  `superset/mcp_service/sql_lab/tool/open_sql_lab_with_context.py:153`, `superset/mcp_service/app.py:94`.

**Assessment:** Most engine-spec/migration sites interpolate *engine/operator-controlled* identifiers
(table/catalog/schema names) into DDL, not attacker-controlled request data — in-scope only if a lower-privileged
principal can influence the interpolated value. **Recommended fix:** for each site confirm inputs are
identifier-validated/quoted (`sqlalchemy.sql.quoted_name` / engine `.quote`) and use bound parameters for
value positions. The **MCP** sites are the highest-priority to verify because they sit on an
LLM-agent-facing surface where inputs may be less trusted.

### 4.2 XSS class — **Low, review**
Bandit `B704` (`markupsafe.Markup` on possibly-untrusted data), 7 sites:
`superset/connectors/sqla/models.py:1459`, `superset/models/dashboard.py:280`,
`superset/models/helpers.py:632,676`, `superset/models/slice.py:351`,
`superset/models/sql_lab.py:522`, `superset/utils/core.py:556`.
These wrap model attributes into `Markup` (mostly for link/label rendering). **Fix:** confirm each wrapped
value is server-controlled, not free-form user text; escape or use safe builders where a user can set the field.
(Historical stored-XSS CVEs, e.g. CVE-2025-55672, make this class worth a spot-check.)

### 4.3 Weak hash (MD5) — **Low, non-security (informational)**
Bandit `B324` High-severity flags at `superset/utils/hashing.py:34`, `superset/key_value/utils.py:98`,
`superset/utils/public_interfaces.py:43,49`, and one migration. All are **cache keys / interface signatures**,
not credential or integrity contexts. **Fix (cosmetic):** pass `usedforsecurity=False` to silence the scanner
and document intent. No exploit path.

### 4.4 Code execution & deserialization — **Medium, review (trusted boundary)**
* `exec(code, module.__dict__)` — `superset/extensions/utils.py:64` (Bandit `B102`, Semgrep `exec-detected`).
  This is the `.supx` extension loader. Executing extension code is **by design**; extensions are an
  operator/Admin-trusted supply-chain decision (see `feat/extensions-security-sandbox` work upstream).
  **Recommendation:** ensure extension install is gated to Admin and documented as a trust boundary;
  not a vuln under the current model, but a high-value target — worth the sandbox effort already in flight.
* `yaml` unsafe load — `superset/examples/utils.py:261` (Bandit `B506`, Semgrep `avoid-pyyaml-load`).
  Examples import path. **Fix:** use `yaml.safe_load` unless full-loader features are required; examples
  content is bundled/operator-supplied but switching to `safe_load` is a cheap hardening.
* `pickle` loads — `superset/key_value/types.py:88` and permalink migration
  `2022-06-27_..._permalink_rename_filterstate.py:59,79` (Bandit `B301`, Semgrep `avoid-pickle`, 8 total).
  Key-value/permalink codec. **Fix:** confirm pickle payloads originate only from Superset-written metadata
  (operator-trusted store); prefer JSON codec for any externally-influenced value.

### 4.5 SSRF / URL handling — **Low, review**
* `urllib.request.urlopen` on a dynamically-built URL — `superset/tasks/utils.py:133` (internal CSRF-token
  fetch to Superset's own `SecurityRestApi.csrf_token`) and `superset/db_engine_specs/lint_metadata.py:111`
  (Bandit `B310`, Semgrep `dynamic-urllib-use-detected`). Both target internal/operator-controlled URLs.
  **Fix:** restrict schemes to `http(s)` and validate host allow-lists if any part becomes user-influenced.
* `superset/db_engine_specs/impala.py:238` uses `http://` (Semgrep `request-with-http`, INFO) — prefer TLS.
* `url_for(..., _external=True)` in permalink APIs and `utils/oauth2.py:285` — verify `PREFERRED_URL_SCHEME`
  so generated external links aren't downgraded/host-spoofable behind a proxy.
* `B104` bind-all-interfaces at `superset/mcp_service/utils/url_utils.py:30` — dev/bind default; confirm not
  used for a production listener.

### 4.6 `nan-injection` (Semgrep ERROR) — **false positive**
`superset/utils/core.py:361` — flagged `bool(...)` coercion of the `standalone` query param. It only produces
a boolean, no `float('nan')`/eval path. **No action.**

---

## 5. Performance Review

### 5.1 Per-iteration metadata-DB commits — **Medium**
* **`superset/sql/execution/executor.py:177-183`** — inside `execute_sql_with_cursor`, the loop over statements
  updates `query.progress` and calls `db.session.commit()` **once per statement**:
  ```python
  for i, statement in enumerate(statements):
      ...
      query.progress = int(((i + 1) / total) * 100)
      query.set_extra_json_key("progress", f"Running statement {i + 1} of {total}")
      db.session.commit()   # <-- one metadata-DB round trip per statement
  ```
  For a multi-statement script this is N commits on the **metadata** DB (not the analytics DB). Each commit is a
  network round trip + fsync. **Recommendation:** throttle progress writes (e.g., commit every K statements or on
  a time interval), or use a single transaction and only commit progress at coarse checkpoints.

* **`superset/sql_lab.py:513-529`** — the async block loop does `db.session.refresh(query)` **and**
  `db.session.commit()` per block:
  ```python
  for i, block in enumerate(blocks):
      db.session.refresh(query)          # extra SELECT per block (stopped-check)
      ...
      query.set_extra_json_key("progress", msg)
      db.session.commit()                # extra commit per block
  ```
  Same N-round-trip pattern, plus a `refresh` (SELECT) per block for the stopped-status check.
  **Recommendation:** poll the stopped flag from a lighter query or cache/Redis flag rather than a full
  ORM `refresh`, and batch progress commits. (`execute_query` itself also does a `commit()` +
  `refresh()` per statement at `sql_lab.py:273-284`, added deliberately to avoid NullPool idle-connection
  kills — keep, but it compounds the round-trip count.)

### 5.2 Repeated full-dict iteration in importers — **Low**
`superset/commands/importers/v1/examples.py:152-236` (and the analogous `assets.py`) iterate
`configs.items()` **four separate times** (databases → datasets → charts → dashboards), each pass re-scanning
every file entry with `startswith(...)`. For large import bundles this is O(4·N) string scans plus per-item
`import_*` DB writes. **Recommendation:** bucket entries by prefix in a single pass, then process each bucket.
Impact is bounded (import is not a hot path), hence Low.

### 5.3 Correctness/perf issues tracked upstream (see §2.1)
* **#41623** — owner association tables (`dashboard_user`, `slice_user`, `sqlatable_user`) lack a
  `UniqueConstraint`, allowing duplicate owner rows; duplicates then break owner removal (422). Both a
  data-integrity bug and a symptom of un-deduplicated owner writes.
* **#41687** — export endpoints inherit a 1-year `Cache-Control`, serving stale exports (cache correctness).
* **#41745** — SSH tunneling latency (labelled `#bug:performance`), related to the `paramiko`/`sshtunnel`
  stack also implicated in F2.

### 5.4 No N+1 confirmed in the audited hot paths
The executor/SQL-Lab paths issue queries against the **analytics** DB via a single shared connection/cursor
(`get_raw_connection` + one `cursor`), so there is no classic ORM N+1 there. The metadata-DB cost is the
per-iteration commit/refresh pattern in §5.1, not a lazy-load fan-out. A broader ORM-relationship N+1 sweep
(eager vs. lazy loading in list APIs) was **not** exhaustively performed and is recommended as follow-up.

---

## 6. Methodology & Reproduction

```bash
# 1. Divergence
git remote add upstream https://github.com/apache/superset.git && git fetch upstream
git log --oneline HEAD..upstream/master
git log --oneline upstream/master..HEAD

# 2. Issues / advisories
gh issue list --repo apache/superset --state open --limit 40
gh api "/advisories?ecosystem=pip&affects=apache-superset&per_page=100"

# 3a. Backend deps
pip install pip-audit
pip-audit -r requirements/base.txt --desc
sudo apt-get install -y pkg-config default-libmysqlclient-dev   # needed for mysqlclient metadata build
pip-audit -r requirements/development.txt

# 3b. Frontend deps
( cd superset-frontend && npm audit --omit=dev )

# 4. Static analysis
pip install bandit semgrep
bandit -r superset/ -ll
semgrep --config p/python --config p/security-audit --config p/owasp-top-ten superset/
```

### Limitations
* Static-analysis findings are **candidate** issues; confirmation requires tracing each input to a principal in
  `SECURITY.md`'s role/capability matrix (per `AGENTS.md`, automated findings must name the violated row and
  assumed principal — done qualitatively above, not exhaustively per-site).
* `paramiko` has no non-4.x fix; `sshtunnel` compatibility must be checked before bumping.
* No dynamic testing, no full ORM relationship-loading (N+1) sweep, no frontend dependency deep-scan beyond
  `npm audit`. These are recommended follow-ups.
