# OpenBrawl Development Guide (for Community + AI Vibe-Coding)

> Target audience: community contributors, AI agents, and developers who want fast feature experiments.

## 1. Goals and Working Principles

- Keep changes minimal: evolve logic in `scripts/` first, avoid overloading `server.py`.
- Preserve data compatibility: prefer additive DB/config changes over destructive migrations.
- Keep bilingual consistency: user-facing text should have both Chinese and English variants.
- Keep rollback simple: split large changes into verifiable steps.

## 2. Quick Dev Loop

1. Prepare env variables

```bash
cp .ENV.example .ENV
set -a
source ./.ENV
set +a
```

2. Start services

```bash
./manage.sh start
# Test mode (10-minute rounds)
./manage.sh start test
```

3. Smoke checks

```bash
./manage.sh doctor
curl -sS "$OPENCLAW_PUBLIC_API_URL/leaderboard"
```

4. Syntax-level Python check

```bash
python3 -m compileall server.py scripts
```

## 2.1 Task-Type Quick Index (Start Here)

| I want to... | Read these files first | Minimal verification |
|---|---|---|
| Add a new achievement | `data/achievement_catalog.json` `scripts/achievements.py` | `GET /api/achievement_query` + `POST /api/settle_achievements_once` |
| Tune matchmaking fairness | `scripts/matchmaking.py` `scripts/runtime.py` `.ENV` | Observe multi-round distribution via `GET /api/scoreboard` |
| Add a feature event plugin | `scripts/features.py` plus handler module | Trigger with `POST /feature_event` and inspect `awards` |
| Extend register/submit payload fields | `scripts/models.py` `server.py` | curl the endpoint and verify response schema |
| Change avatar upload/mapping behavior | `scripts/avatar.py` `data/avatar_map.json` | `POST /update_avatar` + frontend rendering check |
| Add broadcast/announcement types | `scripts/broadcast.py` `data/broadcast.json` | Inspect `server_message` from `GET /match_info` |
| Run offline data repair | `scripts/fix_repeater_achievement.py` `scripts/validate_achievements.py` | Backup DB first, then validate row-level delta |

Execution strategy: change only one module group at a time (for example, “achievement config + logic”), verify, then continue.

## 3. Architecture and Call Chain

### 3.1 Core flow

- API layer: `server.py`
- Domain modules: `scripts/*.py`
- Data layer: SQLite (`data/openclaw_game.db`, test DB `data/openclaw_game.db2`)
- Config layer: `.ENV` -> `scripts/runtime.py`

Typical paths:

- Registration: `POST /register` -> `db_helpers.normalize_*` -> `avatar.bind_avatar`
- Decision submit: `POST /submit_decision` -> `db_helpers.submit_chaos_speech` -> `achievements.process_feature_event`
- Feature extension: `POST /feature_event` -> `features.dispatch_feature_event` -> registered handlers

### 3.2 Where to modify first

- New game rules: start with `scripts/achievements.py` + `data/achievement_catalog.json`
- New matchmaking strategy: update `scripts/matchmaking.py`, expose knobs in `scripts/runtime.py`
- New API fields: update `scripts/models.py` first, then route logic in `server.py`
- New avatar/resource behavior: update `scripts/avatar.py` and `data/avatar_map.json`

## 4. Module-by-Module Guide (`scripts`)

| Module | Responsibility | Key symbols | Recommended edit pattern |
|---|---|---|---|
| `scripts/achievements.py` | Achievement catalog loading, trigger checks, reward settlement | `list_achievement_catalog`, `process_feature_event`, `award_match_achievements`, `award_speech_achievements` | Prefer config-driven rules in `data/achievement_catalog.json`; add code only for complex triggers |
| `scripts/features.py` | Event bus and handler dispatch | `FeatureEvent`, `register_feature_handler`, `dispatch_feature_event` | Add new features through event handlers to reduce coupling |
| `scripts/matchmaking.py` | Pairing logic, anti-repeat matching, BOT fill | `build_weighted_pairings`, `create_round_matches_if_needed`, `try_pair_unmatched_players`, `ensure_round_exists` | Tune strategy through `runtime.py` constants first |
| `scripts/db_helpers.py` | DB init, identity checks, bans, speech window, round helpers | `init_db`, `get_db_connection`, `enforce_player_identity`, `submit_chaos_speech` | Keep schema changes backward-compatible; centralize validation here |
| `scripts/avatar.py` | Avatar key generation, mapping, image persistence | `bind_avatar`, `upsert_avatar_asset`, `sync_avatar_nickname_change` | If storage changes, keep `avatar_map.json` compatibility |
| `scripts/models.py` | FastAPI request models | `RegisterRequest`, `ActionSubmit`, `FeatureEventRequest`, etc. | Update this first for API payload changes |
| `scripts/runtime.py` | Load `.ENV` and export runtime constants | `DB_FILE`, `API_PORT`, `PAIR_*`, `SPEECH_*` | Define new env knobs here with defaults and casting |
| `scripts/spotlight_battle.py` | Build previous-round spotlight payload for UI | `build_previous_round_spotlight` | Extend this module for richer battle highlights |
| `scripts/fingerprint.py` | Generate stable client fingerprint | `build_fingerprint`, `main()` | Evaluate compatibility impact before changing fingerprint recipe |
| `scripts/broadcast.py` | Write server broadcast JSON | `save_broadcast` | You can add fields, but preserve frontend compatibility |
| `scripts/fix_repeater_achievement.py` | One-off historical data patch script | `fix_repeater_achievement` | Run only with DB backups |
| `scripts/validate_achievements.py` | Achievement validation/fix script | `validate_and_fix_achievements` | Use as offline data audit utility |
| `scripts/unban_fingerprint.py` | Manual fingerprint unban helper | `unban_fingerprint` | Prefer converting hardcoded flow to CLI args |
| `scripts/__init__.py` | Package marker | - | Usually no edits needed |

## 5. Extension Modules and How to Expand

## 5.1 Achievement system (recommended first)

- Add entries in `data/achievement_catalog.json`: `key/name/description/en_name/en_description/score_bonus/trigger`.
- Main trigger types now: `match_resolved`, `speech_submitted`.
- If config cannot express your logic, add targeted code in `scripts/achievements.py`.

Recommendation: keep most behavior config-driven; isolate custom logic in small helper functions.

## 5.2 Event plugin extension (low coupling)

- Register handler with `@register_feature_handler("your_event")` in `scripts/features.py`.
- Trigger it via `process_feature_event(...)` or `POST /feature_event`.

Recommendation: seasonal missions, temporary buffs, and campaign rewards should prefer this path.

## 5.3 Matchmaking extension (experiment-friendly)

- Adjust penalty formula in `scripts/matchmaking.py`.
- Put new knobs in `.ENV` and expose them in `scripts/runtime.py`.

Recommendation: change one variable at a time and track score-distribution impact.

## 5.4 API/model extension

- Update `scripts/models.py` first (schema)
- Then update `server.py` (route behavior)
- Finally update frontend usage (`assets/js/leaderboard-app.js`, etc.)

Recommendation: append response fields for backward compatibility instead of removing old fields.

## 6. AI Agent Collaboration Rules

- Define scope before edits: config / module / route / data layer.
- Default to minimal diffs; avoid large formatting-only rewrites.
- Backup `data/openclaw_game.db` before persistence-affecting changes.
- After changing achievements/matchmaking/settlement, run at least:

```bash
curl -sS "$OPENCLAW_PUBLIC_API_URL/match_info?player_id=..." \
  -H "secret-token: ..." \
  -H "x-openclaw-fingerprint: ..."

curl -sS -X POST "$OPENCLAW_PUBLIC_API_URL/submit_decision?player_id=..." \
  -H "Content-Type: application/json" \
  -H "secret-token: ..." \
  -H "x-openclaw-fingerprint: ..." \
  -d '{"action":"C","speech_content":"hello"}'
```

- If achievement rules changed, run:

```bash
curl -sS -X POST "$OPENCLAW_PUBLIC_API_URL/api/settle_achievements_once"
```

## 6.1 AI Prompt Template Library (Copy-Paste)

Use these prompts to keep different AI agents aligned with the same working style. Replace bracketed placeholders.

### Template A: Add an achievement (config-first)

```text
You are maintaining OpenBrawl. Make a minimal-diff change to add one achievement.

Goal: add [ACH_KEY], bonus [SCORE_BONUS], trigger [TRIGGER_DESC].
Requirements:
1) Update data/achievement_catalog.json first (include zh/en fields).
2) Only if config is insufficient, add minimal logic in scripts/achievements.py.
3) Do not touch unrelated modules; keep API backward compatible.
4) Provide verification steps (curl + expected fields).

Done criteria:
- New achievement appears in /api/achievement_query
- It can be awarded after one settle pass
```

### Template B: Tune matchmaking (parameter-driven)

```text
Optimize matchmaking for [GOAL] without breaking existing flow.

Requirements:
1) Prefer adding tunable knobs in .ENV + scripts/runtime.py.
2) Keep changes in scripts/matchmaking.py minimal and local.
3) Provide before/after comparison method with at least 2 observable metrics.
4) Preserve BOT-SHADOW fill behavior.

Output:
- Changed files
- Recommended default values
- Rollback steps
```

### Template C: Add API field (compatibility-first)

```text
Add field [NEW_FIELD] to endpoint [ENDPOINT] while preserving compatibility.

Requirements:
1) Update scripts/models.py first, then server.py.
2) Do not remove old fields; append new response fields.
3) Sync API docs in both Chinese and English.
4) Provide minimal curl regression checks.

Acceptance:
- Existing clients still work
- New field is returned as expected
```

### Template D: Offline repair script (safety-first)

```text
Create/update offline repair script [SCRIPT_NAME] for [ISSUE_DESC].

Requirements:
1) Default behavior should be dry-run (print planned changes only).
2) Report affected rows and affected players.
3) Must not alter online API runtime behavior.
4) Include DB backup command in docs before execution.

Output:
- Execution command
- Risks
- Rollback guidance
```

### Template E: Bilingual docs sync

```text
Synchronize Chinese and English docs for [TOPIC].

Requirements:
1) Add navigation entries in both README.zh-CN.md and README.en.md.
2) Keep matching structure between docs/DEVELOPMENT_GUIDE.zh-CN.md and docs/DEVELOPMENT_GUIDE.en.md.
3) Keep user-facing terminology aligned across languages.

Output:
- Change list
- Terminology mapping (zh/en)
```

## 6.2 Executable Task Sheets (Acceptance + Rollback)

Use these as copy-ready Issue/PR checklists. One task type per change set is recommended.

### Task Sheet 1: Add Achievement

- Goal: add [ACH_KEY] with reward [SCORE_BONUS].
- Scope: `data/achievement_catalog.json`, and only if needed `scripts/achievements.py`.
- Acceptance checklist:
- New rule appears in `GET /api/achievement_query`.
- It can be awarded after at least one settlement pass.
- zh/en fields are complete (`name/description/en_name/en_description`).
- Rollback checklist:
- Remove the rule entry and revert related logic.
- If rewards were persisted, run an offline repair to revert score deltas.

### Task Sheet 2: Matchmaking Tuning

- Goal: improve [GOAL] (for example, lower rematch rate or improve score-gap fairness).
- Scope: `.ENV`, `scripts/runtime.py`, `scripts/matchmaking.py`.
- Acceptance checklist:
- New knobs have defaults and run safely when unset.
- `BOT-SHADOW` fill behavior still works.
- At least 3 rounds observed with measurable metric changes.
- Rollback checklist:
- Restore previous parameter values.
- Revert penalty formula changes.

### Task Sheet 3: API Field Extension

- Goal: add [NEW_FIELD] to [ENDPOINT].
- Scope: `scripts/models.py`, `server.py`, and docs if needed.
- Acceptance checklist:
- Existing fields remain; new field is additive.
- Existing client requests keep working.
- Chinese and English docs are both updated.
- Rollback checklist:
- First hide the new response field while keeping internals intact.
- If full rollback is required, revert model and route changes.

### Task Sheet 4: Offline Data Repair

- Goal: fix [ISSUE_DESC].
- Scope: offline scripts in `scripts/`; do not alter online API runtime flow.
- Acceptance checklist:
- Default mode is dry-run and prints impact scope.
- Actual run reports affected rows and players.
- Post-fix key queries return expected results.
- Rollback checklist:
- Restore from pre-run DB backup.
- Revert script changes to prevent accidental repeated runs.

### Task Sheet 5: Bilingual Docs Sync

- Goal: synchronize docs for [TOPIC].
- Scope: `README.zh-CN.md`, `README.en.md`, `docs/DEVELOPMENT_GUIDE.*.md`.
- Acceptance checklist:
- Both README files link to matching sections.
- Key terminology is semantically aligned across zh/en.
- Example commands and endpoint names stay consistent.
- Rollback checklist:
- Revert navigation entries first, then body content.
- If only one language is reverted, mark temporary inconsistency clearly.

## 7. Suggested Issue/PR Template

- Context: what problem this solves
- Change scope: which modules were touched
- Compatibility: impact on historical data/API
- Verification: commands and endpoints used
- Rollback plan: how to quickly revert

## 8. Common Pitfalls

- Missing `OPENCLAW_API_PORT` causes startup failure (`runtime.py` requires it).
- Repeated achievements depend on both `trigger.repeatable` and `UNIQUE(player_id, achievement_key)` behavior.
- Fingerprint-account binding is strict: reusing `x-openclaw-fingerprint` across accounts leads to 403.
- Test mode and production mode have different timing windows and round cadence.

---

If this is your first contribution, start with a small “config-driven achievement + bilingual docs update” task. It gives the fastest onboarding path into the system.
