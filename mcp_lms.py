# -*- coding: utf-8 -*-
"""MCP server exposing the LMS harness tools to Cursor (stdio transport).

Register in Cursor (user-level %USERPROFILE%\\.cursor\\mcp.json or project
.cursor/mcp.json):
  { "mcpServers": { "lms": {
      "command": "python",
      "args": ["c:\\\\Users\\\\Lanutrix\\\\Downloads\\\\eng\\\\mcp_lms.py"] } } }

Tools: courses, open_course, map, context, assign, quiz_status, start_attempt,
       collect_questions, fill_answers, submit_attempt, get_review, web_search,
       read_page, close_search_tab.
Requires Firefox running with -marionette (python lms.py can't start it for you —
run: "C:\\Program Files\\Mozilla\\Firefox\\firefox.exe" -marionette).
"""
import json, os, sys, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lmslib as L

try:
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def t_str(name, desc, props, required):
    return {
        'name': name,
        'description': desc,
        'inputSchema': {
            'type': 'object',
            'properties': props,
            'required': required,
        },
    }


TOOLS = [
    t_str('lms_courses', 'List all courses on the LMS dashboard (id + name). Engines: Firefox (default) '
          'or Chrome — set "LMS_ENGINE": "chrome" in the mcp.json env to switch.', {}, []),
    t_str('lms_open_course', 'Open a course page in the browser tab.',
          {'course_id': {'type': 'string', 'description': 'numeric course id'}}, ['course_id']),
    t_str('lms_map', 'List all modules of a course (type/name/id/section) — quizzes, resources, assignments...',
          {'course_id': {'type': 'string'}}, ['course_id']),
    t_str('lms_context',
          'Download ALL reference materials of a course into context folder: '
          'resource/pdf files, assignment texts + attached files, pages, folders. '
          'Returns the module manifest and an index.md path. Safe (no attempts started).',
          {'course_id': {'type': 'string'},
           'outdir': {'type': 'string', 'description': 'optional output dir'}}, ['course_id']),
    t_str('lms_assign',
          'Read an assignment (text) and download all attached files.',
          {'assign_id': {'type': 'string'},
           'outdir': {'type': 'string'}}, ['assign_id']),
    t_str('lms_quiz_status', 'Quiz status: attempts allowed/used, grades, buttons. Read-only.',
          {'quiz_id': {'type': 'string'}}, ['quiz_id']),
    t_str('lms_start_attempt', 'Start a quiz attempt (clicks Пройти тест / Начать попытку). '
          'Returns attempt page url. Call collect_questions afterwards.',
          {'quiz_id': {'type': 'string'}}, ['quiz_id']),
    t_str('lms_collect_questions',
          'Collect all questions of the started attempt: texts, prev-contexts, '
          'options for selects, text fields. Mind: do not leave attempt hanging — '
          'fill and submit when done.',
          {}, []),
    t_str('lms_fill_answers',
          'Fill answers of the started attempt. answers={"select": {field_name: option_text}, '          '"text": {field_name: value}}. Option matched exactly first, then by substring. '
          'Never invent field names — use the ones from collect_questions.',
          {'answers': {'type': 'object'}}, ['answers']),
    t_str('lms_submit_attempt',
          'Finish and submit the current attempt (with 502 recovery), then return the review.',
          {}, []),
    t_str('lms_get_review', 'Read current attempt review (grades per question).', {}, []),
    t_str('lms_web_search',
          'Web search in a SEPARATE browser tab (DuckDuckGo lite) — the LMS tab never leaves '
          'the LMS site. Use when the course materials lack an answer.',
          {'query': {'type': 'string'}}, ['query']),
    t_str('lms_read_page',
          'Read text of a web page in the same separate search tab (leaves LMS untouched).',
          {'url': {'type': 'string'},
           'max_chars': {'type': 'number', 'description': 'default 12000'}}, ['url']),
    t_str('lms_close_search_tab', 'Close the search tab and switch back to the main tab.', {}, []),
]


def call_tool(name, args):
    engine = os.environ.get('LMS_ENGINE', 'firefox') or 'firefox'
    lms = L.LMS(engine=engine)
    if lms.engine == 'chrome':
        lms.sync_firefox_cookies()  # idempotent: keeps LMS session alive in chrome profile
    if name == 'lms_courses':
        return lms.courses()
    if name == 'lms_open_course':
        return lms.open_course(str(args['course_id']))
    if name == 'lms_map':
        return lms.map_modules(str(args['course_id']))
    if name == 'lms_context':
        return lms.build_context(str(args['course_id']), outdir=args.get('outdir'))
    if name == 'lms_assign':
        outdir = args.get('outdir') or os.path.join(L.CTX_ROOT, 'assignments', str(args['assign_id']))
        return lms.download_assign(str(args['assign_id']), outdir)
    if name == 'lms_quiz_status':
        return lms.quiz_status(str(args['quiz_id']))
    if name == 'lms_start_attempt':
        return lms.start_attempt(str(args['quiz_id']))
    if name == 'lms_collect_questions':
        return lms.collect()
    if name == 'lms_fill_answers':
        a = args['answers']
        if 'select' not in a and 'text' not in a:
            return {'error': 'answers must be {"select": {...}} and/or {"text": {...}}'}
        return lms.fill(a)
    if name == 'lms_submit_attempt':
        return lms.submit_attempt()
    if name == 'lms_get_review':
        return lms.review()
    if name == 'lms_web_search':
        return lms.web_search(str(args['query']))
    if name == 'lms_read_page':
        return lms.read_url(str(args['url']), int(args.get('max_chars') or 12000))
    if name == 'lms_close_search_tab':
        return {'closed': lms.close_search_tab()}
    raise ValueError('unknown tool: %s' % name)


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        method = req.get('method', '')
        req_id = req.get('id')
        if req_id is None:
            continue  # notification — nothing to answer
        if method == 'initialize':
            send({'jsonrpc': '2.0', 'id': req_id, 'result': {
                'protocolVersion': '2024-11-05',
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'lms-harness', 'version': '1.0.0'},
            }})
        elif method == 'tools/list':
            send({'jsonrpc': '2.0', 'id': req_id, 'result': {'tools': TOOLS}})
        elif method == 'tools/call':
            try:
                params = req.get('params', {})
                result = call_tool(params.get('name'), params.get('arguments') or {})
                send({'jsonrpc': '2.0', 'id': req_id, 'result': {
                    'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}]}})
            except Exception as e:
                L.log('MCP tool error: %s' % traceback.format_exc())
                L.flush()
                send({'jsonrpc': '2.0', 'id': req_id, 'result': {
                    'content': [{'type': 'text', 'text': 'ERROR: %r' % e}], 'isError': True}})
        elif method == 'ping':
            send({'jsonrpc': '2.0', 'id': req_id, 'result': {}})
        else:
            send({'jsonrpc': '2.0', 'id': req_id,
                  'error': {'code': -32601, 'message': 'method not found: %s' % method}})


if __name__ == '__main__':
    main()