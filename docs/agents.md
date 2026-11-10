# AI agents on your computer

research_brain can use an AI agent CLI you already have and are signed in to, so you don't need an API key.
Whichever one you pick, research_brain runs it **with no tools**. The model gets the text of your question and
the passages from your library, and it answers in text. It can't run commands, open files, browse the web
or start MCP servers. Research mode works the same way: the model replies with small JSON actions and
research_brain runs them against your library itself (`app/agents/research.py`).

This page covers the CLI adapters in `app/agents/` (`codex.py`, `gemini.py`, `copilot.py`, `opencode.py`,
`qwen.py`, `cursor.py`, all built on `_cli.py`). Claude Code is in `app/claude_cli.py`. Ollama and
OpenAI-compatible local servers are in `ollama.py` and `openai_local.py`.

## What every adapter does

- **A private, empty working folder** for each call. It is a fresh `0700` folder under
  `~/.cache/research_brain/` (`~/Library/Caches/research_brain/` on macOS), and no folder above it is
  writable by other users. It holds only the lockdown files the adapter writes, and it is deleted afterwards.
  Agents read instruction files (`AGENTS.md`, `GEMINI.md`, ...) and project config from their working
  folder, so nothing can be planted next to them.
- **A scrubbed environment.** These are never passed on: `NODE_OPTIONS`, `NODE_PATH`, `LD_PRELOAD`,
  `BASH_ENV`, research_brain's own `RB_AGENT_*`, and each CLI's variables that could approve tools, add
  config or point it at other settings (listed per agent below). Telemetry and auto-update are turned off
  wherever the CLI has a switch for it.
- **The strictest non-interactive mode**, with every switch the CLI has for removing tools, MCP servers,
  extensions, hooks, skills, plugins and instruction files. Nothing auto-approves: no `--yolo`,
  `--allow-all`, `--force` or `--auto`, and no approval mode that runs tools without asking.
- **Our system prompt** replaces the CLI's coding-agent prompt wherever the CLI allows it.
- **The prompt goes on stdin**, so there is no command-line length limit (Cursor is the exception).
- **A tool guard.** Each CLI's JSON event stream is read line by line. Any tool-call event kills the whole
  process group at once and raises `ToolGuardError`. Where the CLI reports the session's tools at startup
  (Qwen Code, and Copilot's MCP server list), that report is checked before any output.
- **A hard timeout.** The process group is also killed when the call times out or when the consumer goes
  away, for example when the browser tab closes.

### How this was verified

Every supported adapter was checked against the real CLI release listed below on 2026-11-10. The real CLI
ran with the adapter's exact command line, but pointed at a **local mock model server** through its own
custom-endpoint setting. The mock recorded every request the CLI sent. A **planted user config** sat next to
it: a hook that leaves a marker file, an MCP server that leaves a marker file, an extension, skills, and
`AGENTS.md`/`GEMINI.md`/`QWEN.md`/`copilot-instructions.md` files. Each lockdown below passed these checks:
the recorded requests offer **no tools**, they carry our system prompt and none of the planted
instructions, no hook ran, and no MCP server started. We also weakened the lockdown on purpose, for
example by dropping Copilot's hook switch or Qwen's tool exclusions, and the checks failed.

`tests/test_agent_cli_adapters.py` repeats all of this:

- With **fake executables** (always run). These check the exact flags, that no auto-approve flag is
  present, environment scrubbing, the private folder, the streaming parse, that a tool event kills the
  process group, timeout and early-close kills, error messages and sign-in detection.
- Against the **real CLIs** when they are installed (skipped otherwise; set `RB_TEST_REAL_CLIS=0` to skip
  them). These check that every flag we rely on still appears in `--help` (`codex features list` too), and
  they run the mock-server checks above. A CLI update that drops or renames a switch fails these tests.

## Supported agents

### Codex CLI (OpenAI)

| | |
| --- | --- |
| Verified against | `codex-cli 0.167.1` |
| Install / sign in | `npm i -g @openai/codex` (or `brew install codex`), then run `codex` and sign in with ChatGPT |
| Cost | Uses your ChatGPT plan; counts against that plan's Codex limits. |
| Invocation | `codex exec --json --strict-config --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check --sandbox read-only --cd <private> --color never [-m MODEL] -c approval_policy="never" -c web_search="disabled" -c project_doc_max_bytes=0 -c mcp_servers={} -c skills.bundled.enabled=false -c skills.include_instructions=false -c model_instructions_file=<private file> -c history.persistence="none" -c analytics.enabled=false -c feedback.enabled=false -c check_for_update_on_startup=false --disable shell_tool --disable unified_exec --disable view_image --disable multi_agent --disable goals --disable apps --disable plugins --disable hooks --disable memories --disable image_generation --disable browser_use --disable computer_use --disable in_app_browser --disable tool_suggest --disable sleep_tool --disable skill_search --disable shell_snapshot --disable remote_plugin --disable code_mode_host -` (prompt on stdin) |
| How tools are removed | Every tool feature is disabled; `web_search="disabled"`; no MCP servers. `--ignore-user-config` skips `~/.codex/config.toml`, with its MCP servers, profiles, hooks and notify command; sign-in still comes from `CODEX_HOME`. Bundled and user skills are off. `--strict-config` makes an unknown `-c` key an error, and an unknown `--disable` feature is an error too, so a setting renamed in a later release fails closed instead of being ignored. The read-only sandbox with approval `never` is a second layer. Our instructions replace the coding-agent prompt. |
| Tool guard | Any `--json` item other than `agent_message`, `reasoning` or `error` (such as `command_execution`, `file_change`, `mcp_tool_call` or `web_search`) kills the process group. |
| Env | `OPENAI_API_KEY`, `CODEX_API_KEY`, `OPENAI_BASE_URL` and other `CODEX_*` variables are dropped (`CODEX_HOME` is kept). |
| Sign-in check | `codex login status` (no model call) |
| Residual risk | One inert tool remains, `request_user_input` (it asks the user a question; nobody answers in `exec`). Your own global `~/.codex/AGENTS.md` is still added to the prompt; 0.167 has no switch for it. It can steer answers but can't add tools. |

### Gemini CLI (Google)

| | |
| --- | --- |
| Verified against | Gemini CLI `0.68.0` |
| Install / sign in | `npm i -g @google/gemini-cli`, then run `gemini` once and choose "Sign in with Google" |
| Cost | Free with a personal Google account, within a daily request limit (about 1,000 a day per Google's Gemini CLI docs at the time of writing; limits change). |
| Invocation | `gemini --skip-trust --approval-mode default --extensions none --allowed-mcp-server-names __rb_no_mcp__ --output-format stream-json --admin-policy <private deny-all.toml> [-m MODEL] -p ""` (prompt on stdin) |
| How tools are removed | An admin-tier policy denies `toolName = "*"`, so the model sees no tools. By default a headless session offers `read_file`, `list_directory`, `grep_search`, `glob` and `google_web_search`, and runs them without asking. `<private>/.gemini/settings.json` holds workspace settings, which override your user settings; only this private folder is trusted, through `GEMINI_CLI_TRUST_WORKSPACE=true`. They set: a sentinel `tools.core` allowlist, `mcp.allowed` sentinel, `mcpServers` empty, admin MCP/extensions/skills off, `hooksConfig.enabled=false`, skills off, telemetry and usage statistics off, auto-update off, checkpointing off, sub-agents and auto-memory off, `model.maxSessionTurns=1`, `advanced.ignoreLocalEnv`. `GEMINI_SYSTEM_MD` holds our system prompt. |
| Tool guard | Any `tool_use` or `tool_result` event kills the process group. |
| Env | `GEMINI_CLI_SYSTEM_SETTINGS_PATH`, `GEMINI_CLI_SYSTEM_DEFAULTS_PATH`, `GEMINI_SYSTEM_MD`, `GEMINI_WRITE_SYSTEM_MD`, `GEMINI_SANDBOX`, `SEATBELT_PROFILE`, `GEMINI_CLI_TRUST*`, `GEMINI_CLI_IDE*` and `GEMINI_TELEMETRY_*` are dropped or replaced. |
| Sign-in check | `~/.gemini/oauth_creds.json` or a Gemini/Vertex key in the environment; otherwise "unknown" (the credentials may be in the keychain). |
| Residual risk | Gemini always loads `GEMINI.md` context files: your own `~/.gemini/GEMINI.md`, and any `GEMINI.md` in your home folders above the private folder. The CLI only ever *adds* context file names, so there's no way to switch this off. Chats may be kept in `~/.gemini/tmp`. Your `~/.env` and `~/.gemini/.env` are still read. None of this can add tools. |
| Why not system settings | Gemini accepts `GEMINI_CLI_SYSTEM_SETTINGS_PATH` only if the file and every folder above it are owned by root. For a normal user the file is skipped, and then your hooks run. Verified: with the flags alone, a planted user hook still ran. With the workspace settings it didn't. |

### GitHub Copilot CLI

| | |
| --- | --- |
| Verified against | GitHub Copilot CLI `1.0.109` |
| Install / sign in | `npm i -g @github/copilot` (or `brew install copilot-cli`), run `copilot`, then `/login` |
| Cost | Uses your GitHub Copilot plan (each request counts against its allowance); GitHub has a free Copilot plan with a small monthly allowance. |
| Invocation | `copilot --output-format json --stream on --available-tools __rb_no_tools__ --no-custom-instructions --disable-builtin-mcps --no-ask-user --disallow-temp-dir --disable-client-telemetry --no-auto-update --log-level none --trust-folder --deny-tool shell --deny-tool write --deny-tool read --deny-tool url --deny-tool memory [--disable-mcp-server NAME ...] [--model MODEL]`, with our instructions and then the prompt on stdin (piped stdin is a non-interactive prompt) |
| How tools are removed | `--available-tools` is the allowlist the model sees, set to a sentinel name. An *empty* value is ignored by 1.0.109 and leaves every tool on: bash, view, create, edit, grep, glob, task, sql, skill, ... Read-only shell commands run without asking even in prompt mode. `--deny-tool` rules beat every allow rule. There are no custom instructions, no built-in MCP servers, and every server in `~/.copilot/mcp-config.json` gets `--disable-mcp-server`. Personal hooks (`~/.copilot/hooks`, `settings.json`) can only be stopped by a trusted folder's `.github/copilot/settings.json` `{"disableAllHooks": true}`. So the adapter writes that file into the private folder and trusts that folder for this one run (`--trust-folder`). It refuses to run if the folder is inside a git repository, because trust would also load the repository root's config. |
| Tool guard | `assistant.tool_call_delta`, any `tool.*`, `subagent.*` or `hook.*` event, a message with `toolRequests`, or an MCP server reported in any state other than `disabled` kills the process group. |
| Env | Forced: `COPILOT_AUTO_UPDATE=false`, `COPILOT_PLUGIN_DIR_ONLY=true` (no plugin discovery), `COPILOT_TELEMETRY_DISABLED=true`, `COPILOT_DISABLE_LOGIN_SHELL_ENV=1` (no `bash -l` to read your profile). Dropped: `COPILOT_ALLOW_ALL`, `COPILOT_ASSISTED_APPROVAL`, `COPILOT_SKILLS_DIRS`, `COPILOT_CUSTOM_INSTRUCTIONS_DIRS`, `GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS`/`_WORKSPACE_MCP`, hook/web-fetch localhost switches, OTel exporters. `GH_TOKEN`/`GITHUB_TOKEN` are kept because they are Copilot's own sign-in. |
| Sign-in check | A token in the environment, or a signed-in user in `~/.copilot/config.json`; otherwise "unknown" (keychain). |
| Residual risk | Copilot keeps a session record under `~/.copilot/session-state`, and there's no switch to turn it off. An MCP server defined outside `mcp-config.json` (for example by an installed plugin) would be started before the guard sees it in the startup event and stops the run. There's no system-prompt flag, so our instructions are put in front of the prompt. |

### OpenCode

| | |
| --- | --- |
| Verified against | opencode `1.19.5` |
| Install / sign in | `npm i -g opencode-ai` (or `brew install sst/tap/opencode`); `opencode auth login` for providers you pay for |
| Cost | Free models are offered through OpenCode (which ones changes), or it uses a provider you sign in to with `opencode auth login`. |
| Invocation | `opencode run --pure --agent rb --format json --title research_brain [-m provider/model]` (prompt on stdin) |
| How tools are removed | `XDG_CONFIG_HOME` points at the private folder, so your `~/.config/opencode` (MCP servers, plugins, agents, commands, `AGENTS.md`) is not read; sign-ins in the data folder still are. Our `OPENCODE_CONFIG` sets `permission: {"*": "deny"}`, `tools: {"*": false}`, `mcp: {}`, `instructions: []`, `plugin: []`, `share: "disabled"`, autoupdate/LSP/formatter/snapshot off, and a primary agent `rb` whose prompt is ours and whose permissions deny everything. Also set: `OPENCODE_PERMISSION={"*":"deny"}`, `OPENCODE_DISABLE_PROJECT_CONFIG`, `OPENCODE_DISABLE_CLAUDE_CODE` (no `~/.claude/CLAUDE.md` or skills), `OPENCODE_DISABLE_DEFAULT_PLUGINS`, `OPENCODE_PURE`/`--pure`, `OPENCODE_DISABLE_AUTOUPDATE`, `OPENCODE_DISABLE_LSP_DOWNLOAD`, `OPENCODE_DISABLE_EXTERNAL_SKILLS`. When the model tries a tool anyway, OpenCode answers "Available tools: ." `opencode run` opens no listening port (checked). |
| Tool guard | Any `tool_use` event kills the process group. |
| Env | Every other `OPENCODE_*` variable (`OPENCODE_AUTO_SHARE`, `OPENCODE_CONFIG_CONTENT`, `OPENCODE_SERVER_PASSWORD`, ...) is dropped. |
| Sign-in check | `auth.json` in OpenCode's data folder has entries; otherwise "unknown" (free models need no sign-in). |
| Residual risk | Sessions are stored in OpenCode's local database (no switch). Custom providers defined only in your `opencode.json` aren't available, because that file isn't read. |

### Qwen Code

| | |
| --- | --- |
| Verified against | Qwen Code `0.27.2` |
| Install / sign in | `npm i -g @qwen-code/qwen-code`, run `qwen` and set up authentication |
| Cost | Its free Qwen OAuth tier ended in April 2026; needs a Qwen/Alibaba Cloud plan or an API key set up in Qwen Code. |
| Invocation | `qwen --output-format stream-json --include-partial-messages --safe-mode --chat-recording false --exclude-tools <all ~70 built-in tool names> --max-tool-calls 0 --max-session-turns 1 --approval-mode default --allowed-mcp-server-names __rb_no_mcp__ --extensions none --system-prompt <ours> [-m MODEL]` (prompt on stdin) |
| How tools are removed | `--safe-mode` turns off hooks, extensions, skills, MCP servers and `QWEN.md`, and keeps your settings so your sign-in still works (`--bare` would drop the sign-in too). Safe mode ignores `--core-tools`, so every built-in tool is excluded by name. `--max-tool-calls 0` makes the CLI abort at the first tool call. |
| Tool guard | The `init` event lists the session's tools and MCP servers, and both must be empty. Any `tool_use` block or tool result kills the process group. A tool added in a later release and missing from the exclusion list is caught by the `init` check (fail closed) until the list is updated. |
| Env | Forced: `QWEN_TELEMETRY_ENABLED=false`, `QWEN_USAGE_STATISTICS_ENABLED=false` (otherwise it contacts a RUM endpoint), `QWEN_CODE_DISABLE_CRON`/`_LSP`/`_WORKFLOWS`/`_ARTIFACT`, `QWEN_DISABLE_AUTO_TITLE`. Other `QWEN_*` variables are dropped, except `QWEN_HOME`, `QWEN_API_KEY`, `QWEN_MODEL`, `QWEN_BASE_URL` and `QWEN_DEFAULT_AUTH_TYPE`. |
| Sign-in check | `~/.qwen/oauth_creds.json` or an auth type in `~/.qwen/settings.json`. |
| Residual risk | `~/.qwen/.env` is still read. You need a paid plan or a key. |

## Experimental (off by default)

### Cursor CLI (`cursor-agent`)

Turn it on with `RB_EXPERIMENTAL_AGENTS=cursor`. Until then it shows as not available and refuses to run.

| | |
| --- | --- |
| Verified against | Documentation only (cursor.com/docs/cli, 2026-11-10). The installer is not on npm and couldn't be fetched in the build environment, so the adapter has not been run against a real binary. |
| Cost | Uses your Cursor plan. |
| Invocation | `cursor-agent --print --output-format stream-json --stream-partial-output --mode ask --sandbox enabled --trust --workspace <private> [--model MODEL] "<instructions + prompt>"` |
| Lockdown | `<private>/.cursor/cli.json` denies `Shell(*)`, `Read(**)`, `Write(**)`, `WebFetch(*)` and `Mcp(*:*)`; ask mode (read-only); never `--force`/`--yolo`/`--approve-mcps`. Any `tool_call` event kills the process group. |
| Why experimental | Cursor's print mode "has access to all tools, including write and shell". There's no switch that removes tools from the model, only permission rules and modes. Its `init` event doesn't list tools. A read-only tool call that Cursor runs without asking could finish before it is reported. How it handles your rules, `AGENTS.md` and `~/.cursor/mcp.json` couldn't be verified. The prompt goes in argv, so requests over about 100 kB are refused. |

## Not supported

| Agent | Why |
| --- | --- |
| **Amp** (`amp -x`, Sourcegraph) | Threads are stored on and synced to ampcode.com, and the account can inject workspace plugins, skills, remote MCP servers and secrets from the server side. Tool selection is tied to server-defined modes. A local lockdown couldn't be verified or kept stable. |
| **Aider** | Needs a model API key (or a local server, which research_brain already supports directly through Ollama / OpenAI-compatible). Its workflow edits and commits files and offers to run commands and scrape URLs found in the prompt. A "no-edit" mode still isn't a tool-free model call. |
| **Goose** (Block), **Crush** (Charm), **Cline CLI** | Bring-your-own API key, and their built-in extensions or tools (shell, editor) can't be removed and verified as reliably as the agents above. For a local model, use the Ollama / OpenAI-compatible backends instead. |
| **Kiro CLI**, **Kimi CLI** and others | Not evaluated in this release (not installable in the build environment). Each would need the same mock-server verification before being added. |

## Adding or updating an agent

1. Install the new version and read `--help`.
2. Run its tests: `python3 -m pytest -q tests/test_agent_cli_adapters.py`. The `test_real_*` tests run the
   installed CLI against the mock model server with a planted config. If a flag disappeared, the `--help`
   test names it.
3. Update `verified_version` in the adapter and the table above.
