# sfedu-lms-harness

**English** | [Русский](README.ru.md)

**An MCP server + CLI that lets AI agents (Cursor, Claude Code, Codex, opencode, …) take quizzes/tests on the SFedU IKTIB Moodle LMS** (`lms.sfedu.ru`, "СЭО ИКТИБ ЮФУ") — through your real browser and your existing logged-in session.

## How it works

1. The harness downloads all course materials (PDF glossaries, assignments with files, pages) into `context/`
2. It opens a quiz and collects every question with its options (`tmp/questions.json`)
3. The agent answers using the materials; gaps are closed via web search **in a separate tab** — the quiz tab never leaves `lms.sfedu.ru`
4. The harness fills each field by name, submits the attempt (auto-retrying on server hiccups) and returns the grade

## Quick start

```bash
git clone https://github.com/Lanutrix/sfedu-lms-harness
cd sfedu-lms-harness

python lms.py courses          # your courses (browser auto-launches)
python lms.py context 4031     # grab ALL materials BEFORE any test
python lms.py quiz 41146       # quiz status: attempts, grades
python lms.py start 41146      # begin an attempt
python lms.py collect          # all questions -> tmp/questions.json
python lms.py fill answers.json  # {"select": {field: option}, "text": {field: answer}}
python lms.py submit           # submit + read the grade
```

Chrome instead of Firefox: prefix `--chrome` or `LMS_ENGINE=chrome` (runs on a separate profile; your LMS cookies are carried over from Firefox automatically).

## Installing into agents

The server is a plain stdio MCP server: `python <path>/mcp_lms.py`.

| Agent | Where |
|---|---|
| **Cursor** | `~/.cursor/mcp.json` (or project `.cursor/mcp.json`) |
| **Claude Code** | `claude mcp add lms -- <path>/mcp_lms.py`, or a `.mcp.json` file |
| **Codex CLI** | `~/.codex/config.toml`: `[mcp_servers.lms]` |
| **opencode** | `opencode.json`: the `"mcp"` section |

```json
{
  "mcpServers": {
    "lms": {
      "command": "python",
      "args": ["<path>/sfedu-lms-harness/mcp_lms.py"],
      "env": { "LMS_ENGINE": "firefox" }
    }
  }
}
```

MCP tools mirror the CLI: `lms_courses`, `lms_context`, `lms_quiz_status`, `lms_start_attempt`, `lms_collect_questions`, `lms_fill_answers`, `lms_submit_attempt`, `lms_get_review`, `lms_assign`, `lms_map`, `lms_web_search`, `lms_read_page`…

## Requirements

Python 3.9+ (stdlib only, nothing to `pip install`) and Firefox or Chrome/Edge — the harness launches the browser with the right debugging flag itself; if none is installed you get a clear message, not a silent crash.

## Notes

- The agent invents the answers; the harness only submits what it was given and reports every field it failed to match
- Course materials live in `context/`, reports in `tmp/` — neither is ever committed
- Academic ethics of using automation on your university's LMS are your responsibility

MIT — see [LICENSE](LICENSE).