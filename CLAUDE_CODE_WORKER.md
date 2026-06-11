# Claude Code Worker — Implementation Plan

Add a **subscription-funded Claude Code worker** to Open SWE without breaking
any existing behavior. The orchestrator (deepagents loop) stays on a
non-Anthropic model and delegates heavy implementation to the official
`claude` CLI running inside the sandbox, authenticated with each user's own
`CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`).

This is an **additive** design: every change is gated on a token being present.
With no token configured, the agent behaves exactly as it does today.

## Why this shape

- **Compliance.** Anthropic prohibits custom harnesses (a LangChain client hitting
  `/v1/messages`) from using subscription OAuth tokens, and blocks it at the API.
  The *official* `claude` CLI authenticated with a per-user `setup-token` is the
  one sanctioned path (bills against the plan's Agent SDK credit). So the harness
  that touches Anthropic must be Anthropic's own binary, inside the sandbox.
- **No Claude API.** The orchestrator runs on `openai:gpt-5.5` /
  `google_genai:gemini-3-*` / `fireworks:*` — already supported by the provider
  layer (`agent/utils/model.py`, `init_chat_model`). No Anthropic key anywhere.

## Architecture

```
GitHub/Slack/Linear webhook
        │
        ▼
  deepagents orchestrator        ← non-Anthropic model (gpt-5.5 / gemini-3 / fireworks)
  (routing, todos, PR open,         job shrinks to plumbing + delegation
   Slack/Linear replies)
        │  run_claude_code(repo_dir, prompt)        ← NEW tool
        ▼
  sandbox.execute("… claude -p …")
        │
        ▼
  claude CLI in sandbox          ← Claude Opus via subscription credit
  (reads repo CLAUDE.md/AGENTS.md,   token from ~/.claude-env (NEW per-run setup)
   edits, runs tests, commits)
```

The orchestrator keeps its built-in deepagents tools (`read_file`, `edit_file`,
`execute`, …). `run_claude_code` is an *additional* capability the system prompt
guides it to prefer for multi-file implementation work. If the guidance is
ignored or absent, it codes directly — same as today.

## The one hard constraint: env injection

`sandbox.execute(command)` runs each command as a bare string with **no per-call
env injection** (`agent/integrations/langsmith.py` → `self._sandbox.run(command)`).
Two existing patterns show the way around this:

1. **`git config --global`** (`server.py:_configure_git_identity`, run every run via
   `ensure_sandbox_for_thread`) writes `~/.gitconfig`, read by git regardless of
   shell. → We copy this exactly: write the token to a file once per run.
2. **GitHub proxy** (`langsmith.py:_configure_github_proxy`) injects auth headers on
   outbound github.com traffic so the token never enters the box. → **Does not
   apply to Claude Code**: the CLI needs the token locally, and Anthropic's API
   endpoint isn't proxied. This is why the worker's security posture is strictly
   weaker than the GitHub path (see Security).

**Chosen mechanism:** a new `_configure_claude_code_auth(sandbox, token)` writes
`~/.claude-env` (`export CLAUDE_CODE_OAUTH_TOKEN=…`, mode 600) once per run,
mirroring `_configure_git_identity`. The `run_claude_code` tool sources it:
`bash -lc 'set -a; . ~/.claude-env; set +a; claude -p …'`. Keeps the token out of
`argv` (no leak via process listing) and out of command logs.

## Changes (all additive, all gated)

| # | File | Change | Breaking? |
|---|------|--------|-----------|
| 1 | `Dockerfile` | `RUN npm i -g @anthropic-ai/claude-code` (node 22 already present). Rebuild snapshot → new `DEFAULT_SANDBOX_SNAPSHOT_ID`. | No — extra binary in image |
| 2 | `agent/server.py` | Add `_configure_claude_code_auth(sandbox, token)`; call it next to `_configure_git_identity` (line ~407), **gated `if token`**. Resolve token from profile/env. | No — no-op without token |
| 3 | `agent/tools/run_claude_code.py` (new) + `tools/__init__.py` export + append to `get_agent` tools list (server.py:594) | The worker tool: runs `claude -p "<prompt>" --output-format json --dangerously-skip-permissions` in `repo_dir`, parses result, returns summary. Returns a clear "worker not configured" error if no token. | No — new tool only on main agent; reviewer untouched |
| 4 | `agent/dashboard/profiles.py` + a dashboard route | Store `encrypted_claude_token` (Fernet, same `TOKEN_ENCRYPTION_KEY` + `["oauth_tokens"]` store pattern already used for `encrypted_gh_token`). UI field for users to paste their `setup-token`. | No — new optional field |
| 5 | `agent/prompt.py` + call site (server.py:585) | New optional `worker_enabled: bool` kwarg → appends a delegation section ("prefer run_claude_code for implementation"). Empty when disabled. | No — gated, default off |
| 6 | `.env` | `LLM_FALLBACK_MODEL_ID` set to a non-Anthropic model. **Already done** — neutralizes `fallback_model_id_for()` returning `anthropic:claude-opus-4-5` for openai primaries. | No — env only |

**Untouched:** reviewer graph, analyzer graph, every existing tool/middleware,
model-resolution precedence, the GitHub proxy path.

## No-breaking-changes guarantee

With no Claude token configured anywhere:
- #2 is a no-op (gated on token presence).
- #3's tool, if ever called, returns a clean "not configured" message — it can't
  be called unintentionally because #5's prompt section (which advertises it) is
  empty when disabled.
- #5 renders nothing.
- The orchestrator uses its own deepagents tools exactly as today.

The reviewer (PR reviews) never receives the new tool or prompt section, so PR
review behavior is identical regardless of worker config. (Note: reviews still
run on the orchestrator model, e.g. gpt-5.5 — the subscription path covers
*feature-building*, not the reviewer loop.)

## Build order

1. **Dockerfile + snapshot** (#1). Verify `claude --version` in the sandbox.
2. **Single-token PoC** (#2 + #3) with one token from `CLAUDE_CODE_OAUTH_TOKEN`
   env (skip per-user storage). Prove end-to-end: orchestrator delegates a real
   change, worker edits + commits, orchestrator opens the PR.
3. **Prompt delegation** (#5) — tune until the orchestrator reliably hands
   implementation to the worker and keeps plumbing for itself.
4. **Per-user tokens** (#4) — encrypted profile field + dashboard UI, so each
   teammate brings their own subscription (required for ToS compliance at team
   scale).
5. **Hardening** — best-effort delete of `~/.claude-env` at run end; surface
   "credit exhausted" as a clean Slack/Linear message.

## Security caveats (must surface to users)

- **Token lives in the sandbox during the run.** Unlike the GitHub proxy, the CLI
  needs the token locally, so a malicious repo or prompt-injected issue could read
  `~/.claude-env` and exfiltrate it. Mitigations: per-user tokens (blast radius =
  one subscription), rotation on suspicion, best-effort file delete at run end.
- **Per-user, not pooled.** One shared token offered to many users is the ToS
  violation; each subscriber's own `setup-token` is the compliant path.
- **Credit ceiling.** When a user's monthly Agent SDK credit depletes, worker runs
  fail — surface that explicitly rather than silently degrading.
