# Agent packs

The studio is usable three ways, and all three end at the same ten tools:

| client | file | where it goes |
|---|---|---|
| Claude Code | `claude-code/.mcp.json` | project root (or merge into `.mcp.json`) |
| Codex CLI | `codex/config.toml` | merge into `~/.codex/config.toml` |
| Gemini CLI | `gemini/settings.json` | merge `mcpServers` into `~/.gemini/settings.json` |
| awdk / AitherOS | `awdk/aither-cinema/` | pass the directory to `adk.agent_pack.load_agent_pack` |

Install the server once, from the repo root:

```bash
pip install -e "mcp/[dev]"
cinema-mcp --check        # exit 0 = backend + ComfyUI reachable, 2 = backend up, ComfyUI down
```

The client entries all launch `cinema-mcp --transport stdio` with
`CINEMA_URL=http://127.0.0.1:8765`. If `cinema-mcp` is not on PATH, use
`python -m cinema_mcp.server --transport stdio` with the interpreter that has the
package. Nothing needs a key: the backend holds the ComfyUI credentials.

The skill (`../skills/aither-cinema/`) is what teaches the model the vocabulary
and the three recipes. Install it for Claude Code with:

```bash
cp -r skills/aither-cinema ~/.claude/skills/
```

## awdk pack

```bash
python -c "
import sys; sys.path.insert(0, 'path/to/awdk')
from adk.agent_pack import load_agent_pack, Supervisor
m = load_agent_pack('agents/awdk/aither-cinema')
print(m.id, m.protocol, Supervisor()._build_command(m))
"
```

Measured on this tree against a local `awdk` checkout:

```
id       : aither-cinema
framework: native | protocol: mcp
skills   : ['aither-cinema'] | mcp: mcp-server.json
argv     : [...python.exe, '-m', 'cinema_mcp.server', '--transport', 'stdio']
```

`runtime.cmd` is a **module target**, not `cinema-mcp`: the Supervisor runs
`[sys.executable, "-m"] + cmd`, so the pack inherits the venv the package was
installed into. A bare console-script name would be looked up on PATH and could
resolve to a different interpreter.

The pack declares no `secrets`. A Comfy API key, when one is used, belongs to
the backend (`COMFY_API_KEY` / `COMFY_API_KEY_FILE`) and never travels through
the agent manifest.

`brain_pack.yaml` alongside it is the persona (the rig is the story; plan before
you spend a render; a `job_id` is not a finished shot). It is discovered by
`adk.pack_discovery` next to `agent.yaml`, or handed to a workspace with
`AGENT_BRAIN_PACK`.
