# sfedu-lms-harness

English | **[Русский](README.ru.md)**

**MCP server + CLI harness that drives your real browser through an LMS (Moodle™): pulls all course materials, reads and takes quizzes/tests, and searches the web — all through your existing logged-in session.**

Built for (and battle-tested on) the Moodle instance of SFedU IKTiB ("СЭО ИКТИБ ЮФУ", `lms.sfedu.ru`), but the approach works for any Moodle with your real browser cookies.

---

## What it does

| Capability | How |
|---|---|
| 📚 **Course context harvester** | Walks every module of a course: downloads resource PDFs, assignment texts **and attached files**, pages, folder contents → `context/<course>/` + `index.md` manifest |
| 🧪 **Quiz engine** | Status → start attempt → collect questions (texts, options, contexts) → fill answers → submit (with automatic **HTTP 502 recovery**) → read grades |
| 🌐 **Two browser engines** | **Firefox** (Marionette protocol, port 2828) or **Chrome/Edge** (CDP, port 9222) — same API, same tools |
| 🔄 **Browser lifecycle** | Auto-launches the browser with the right debugging flag; restarts Firefox if it was opened without one; politely refuses if no browser is installed |
| 🔐 **Session transfer** | For Chrome: carries your logged-in LMS cookies over from a live Firefox session (via Marionette `WebDriver:GetCookies`) — no manual login needed |
| 🔎 **Web search, untracked** | Search + read pages in a **separate browser tab**; the LMS tab never leaves the LMS domain |
| 🧠 **Agent-first** | Answer generation stays with the agent (you); the harness only drives the browser and proves every field it filled |

Zero Python dependencies — stdlib only (custom Marionette client, custom WebSocket client for CDP).

## Repository layout

```
lmslib.py        core library: engines (Marionette/CDP), LMS ops, quiz engine, downloads
lms.py           CLI harness
mcp_lms.py       MCP server (stdio) — 14 tools
mcp.json.example example MCP configuration
fx.py            standalone legacy Marionette CLI (handy for debugging a live session)
firefox_tabs.py  read Firefox session files to list current tabs (no Marionette needed)
```

Runtime folders (auto-created, git-ignored):

```
context/        downloaded course materials (PDFs, DOCXs, texts + index.md)
tmp/            all throw-away reports, dumps, screenshots, session state
chrome_profile/  dedicated Chrome automation profile (keeps its own LMS login)
archive/        your local one-off scripts, if you keep any
```

## Requirements

- **Python 3.9+** (stdlib only, nothing to `pip install`)
- **Mozilla Firefox** — any recent build, *or*
- **Google Chrome / Microsoft Edge** (Chromium)
- Windows out of the box; macOS/Linux work too (adjust browser paths in `lmslib.py`)

## Quick start (CLI)

```bash
git clone https://github.com/Lanutrix/sfedu-lms-harness
cd sfedu-lms-harness

# list your LMS courses (auto-launches/restarts the browser with the right flag)
python lms.py courses

# download ALL reference materials of a course first (do this BEFORE any test)
python lms.py context 1294

# quiz lifecycle
python lms.py quiz 42038          # read-only status: attempts left, grades
python lms.py start 42038         # begin an attempt
python lms.py collect             # -> tmp/questions.json with all fields
#   ... the agent/you decide the answers using context/ ...
python lms.py fill answers.json   # {"select": {field: option_text}, "text": {field: value}}
python lms.py submit              # finish + submit + 502 recovery
python lms.py result              # grades

# web search in a separate tab (LMS tab stays on the LMS)
python lms.py search "marionette protocol"
python lms.py read https://example.com
python lms.py close-search
```

Use Chrome instead of Firefox by prefixing `--chrome` (or `export LMS_ENGINE=chrome`):

```bash
python lms.py --chrome courses
```

`python lms.py` with no arguments prints the full command reference.

## MCP tools

| Tool | Purpose |
|---|---|
| `lms_courses` | list dashboard courses |
| `lms_open_course` / `lms_map` | open a course / list its modules |
| `lms_context` | download **all** course materials into `context/` |
| `lms_assign` | read an assignment + download attached files |
| `lms_quiz_status` | attempts left, current grades (read-only) |
| `lms_start_attempt` | begin a quiz attempt |
| `lms_collect_questions` | full question dump of the started attempt |
| `lms_fill_answers` | fill `{"select": {...}, "text": {...}}` (exact match, then substring) |
| `lms_submit_attempt` | finish + submit with automatic 502 recovery |
| `lms_get_review` | per-question grades of the attempt |
| `lms_web_search` | web search in a **separate tab** (own tab, closed later) |
| `lms_read_page` | read any URL in that separate tab |
| `lms_close_search_tab` | close the search tab, focus back on the LMS |

## Installing into agents

The server is a plain **stdio MCP server**: `python <repo>/mcp_lms.py`.

> Replace `/path/to/sfedu-lms-harness` below with the absolute path on your machine
> (Windows example: `C:\\Users\\you\\tools\\sfedu-lms-harness\\mcp_lms.py`).

### Cursor

Project scope: create `.cursor/mcp.json` in your project folder —
or user scope: `%USERPROFILE%\.cursor\mcp.json` (macOS: `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "lms": {
      "command": "python",
      "args": ["/path/to/sfedu-lms-harness/mcp_lms.py"],
      "env": { "LMS_ENGINE": "firefox" }
    }
  }
}
```

Restart the Cursor window; the `lms` tools appear in the tool list.

### Claude Code

One-liner (project or user scope):

```bash
claude mcp add lms -- /path/to/sfedu-lms-harness/mcp_lms.py
# or with the chrome engine:
claude mcp add lms --env LMS_ENGINE=chrome -- /path/to/sfedu-lms-harness/mcp_lms.py
```

Or as a portable project file `.mcp.json` in the repo root:

```json
{
  "mcpServers": {
    "lms": { "command": "python", "args": ["/path/to/sfedu-lms-harness/mcp_lms.py"] }
  }
}
```

### OpenAI Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.lms]
command = "python"
args = ["/path/to/sfedu-lms-harness/mcp_lms.py"]
# env = { LMS_ENGINE = "chrome" }   # optional
```

### opencode

`opencode.json` (project) or the global config:

```json
{
  "mcp": {
    "lms": {
      "type": "local",
      "command": ["python", "/path/to/sfedu-lms-harness/mcp_lms.py"],
      "environment": { "LMS_ENGINE": "firefox" }
    }
  }
}
```

### Any other MCP-capable agent (Windsurf, Zed, Cline, Goose, …)

Point its "custom MCP server / stdio" setting at:

```
command: python
args:    ["/path/to/sfedu-lms-harness/mcp_lms.py"]
env:     LMS_ENGINE=firefox   # or "chrome"
```

## Typical end-to-end workflow

```
1. lms_courses / lms_map        — find the course and its quizzes
2. lms_context <course_id>      — grab ALL reference materials first
3. lms_quiz_status <quiz_id>    — check attempts/grades, decide whether to act
4. lms_start_attempt            — open the attempt
5. lms_collect_questions        — dump every field (names, options, contexts)
   (agent answers using context/; anything missing →)
6. lms_web_search / lms_read_page — fill knowledge gaps in a separate tab
7. lms_fill_answers             — fill the form, verified field-by-field
8. lms_submit_attempt           — submit (auto-retries on server hiccups)
9. lms_get_review               — read the grade; retry if attempts remain
```

## The `LMS_ENGINE` switch

- `firefox` (default) — the harness **restarts Firefox with `--marionette`** when it detects the flag missing (your tabs are restored by Firefox).
- `chrome` — runs Chrome/Edge with `--remote-debugging-port` on a **separate profile** (`chrome_profile/`): your daily browser is never touched. On first use the harness copies your logged-in LMS cookies from a live Firefox (falls back to `cookies.sqlite`, then to a friendly "log in once in the opened window" message).

## Notes & disclaimers

- The harness **never invents field values**: it moves exactly what the agent sends and reports every field it could not match.
- All reports, dumps and screenshots go to `tmp/`; downloaded course materials go to `context/`. Neither is committed to git.
- Course materials downloaded into `context/` belong to their authors (SFedU/course staff) — keep them local.
- You are responsible for how you use automation on your university's LMS. Ethics and academic rules are yours to respect — see the "Web search" feature: even there, the harness keeps the test tab strictly on the LMS.

## License

MIT — see [LICENSE](LICENSE).