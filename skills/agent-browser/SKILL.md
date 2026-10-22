---
name: agent-browser
description: Browser automation skill for navigating web pages, inspecting accessible controls, interacting with forms, and validating web applications through the agent-browser CLI. Use when a task requires browser interaction or Web UI testing and the CLI is available in the execution environment.
---

# Agent Browser

Use the `agent-browser` CLI for browser automation only when it is installed and available to the execution environment. The TestPilot skill record provides instructions; it does not install or execute shell tools by itself.

## Setup and current instructions

The upstream project serves its detailed skill instructions from the installed CLI so that commands stay aligned with the installed version. Do not copy a stale full skill from a package cache.

```bash
npx skills add vercel-labs/agent-browser
agent-browser skills get core
```

For a local executable installation, follow the upstream installation instructions and install its browser runtime before running a test. If the environment has no `agent-browser` binary, report that the skill is unavailable instead of claiming a real browser run.

## Safe interaction workflow

1. Confirm the target URL is permitted by the project and execution environment.
2. Open the requested page and inspect its accessible snapshot before interacting.
3. Prefer accessible element references, labels, and roles over coordinates or brittle selectors.
4. Re-snapshot after navigation, dialogs, or major page updates; references may become stale.
5. Enter only user-provided or approved test data. Do not expose credentials in logs or screenshots.
6. Verify the visible result after each meaningful action and capture evidence when requested.
7. Close the browser session when the task is complete and report whether execution was real or simulated.

## Common command pattern

```bash
agent-browser open https://example.test
agent-browser snapshot -i
agent-browser click @e1
agent-browser snapshot -i
agent-browser close
```

Use the exact commands documented by `agent-browser skills get core` for the installed version. Do not invent command flags or claim a successful run without observing its result.

## Browser testing guidance

- Cover the happy path, validation errors, empty states, and relevant navigation.
- Assert user-visible text, control state, and URL changes rather than relying on arbitrary sleeps.
- Wait for a specific page state where possible; record genuine timeouts and console errors as failures.
- Avoid destructive actions against production data. Use a test environment or obtain explicit authorization first.
- Keep evidence minimal and redact tokens, passwords, personal data, and private URLs before sharing.

## Upstream reference

- Skill installation and runtime-served instructions: https://github.com/vercel-labs/agent-browser
- Install the discovery skill with `npx skills add vercel-labs/agent-browser`.
