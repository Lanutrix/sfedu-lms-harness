# -*- coding: utf-8 -*-
"""LMS harness CLI (over Firefox/Marionette).

Workflow for a quiz:
  1) python lms.py context <course_id>     # download ALL course materials first
  2) python lms.py quiz <quiz_id>          # check attempts/grades
  3) python lms.py start <quiz_id>         # start attempt, then
  4) python lms.py collect                 # -> questions.json
     ... analyze with context (agent brain); if info missing ->
  5) python lms.py search "query"          # web search in a SEPARATE tab
     ... python lms.py read <url>          # read a page in that same search tab
  6) python lms.py fill answers.json      # {"select": {...}, "text": {...}}
  7) python lms.py submit                 # finish+submit with 502 recovery
  8) python lms.py result                 # grades

Other:
  courses | open <id> | map [<id>] | assign <id> [outdir] | tabs | close-search
Everything is also written to harness_report.txt (utf-8).
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lmslib as L

LIBDIR = os.path.dirname(os.path.abspath(__file__))


def save_json(name, data):
    p = os.path.join(L.TMPDIR, name)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return p


def brief(data):
    """Compact single-line summary for stdout."""
    try:
        if isinstance(data, list):
            return 'list[%d]' % len(data)
        if 'modules' in data:
            return 'course modules: %d, outdir: %s' % (data.get('module_count', len(data['modules'])), data.get('outdir', ''))
        if 'questions' in data:
            return 'questions: %d, fields: %d, saved: questions.json' % (
                data.get('questionCount', 0),
                sum(len(q.get('fields', [])) for q in data.get('questions', [])))
        if 'results' in data:
            return 'search results: %d (search tab)' % len(data['results'])
        if 'bad' in data:
            return 'fields filled, bad: %d / verify in report' % len(data.get('bad', []))
        if 'saved' in data:
            return 'files saved: %d' % len(data.get('saved', []))
        if isinstance(data, dict):
            return '; '.join('%s=%s' % (k, str(v)[:60]) for k, v in list(data.items())[:5])
        return str(data)[:120]
    except Exception as e:
        return 'brief-error: %r' % e


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    engine = os.environ.get('LMS_ENGINE', 'firefox') or 'firefox'
    if args[0] in ('--chrome', '--edge'):
        engine = 'chrome'
        args = args[1:]
    elif args[0] == '--firefox':
        engine = 'firefox'
        args = args[1:]
    cmd = args[0] if args else ''
    if not cmd:
        print(__doc__)
        return 0
    try:
        lms = L.LMS(engine=engine)
        if lms.engine == 'chrome':
            st = lms.sync_firefox_cookies()
            if st.get('logged_in') is False:
                print('WARN:', st.get('note', 'залогинься в LMS в окне Chrome'))
    except RuntimeError as e:
        print('NO_BROWSER:', e)
        return 1
    except Exception as e:
        print('CONNECT_ERROR: %r (is the browser running with debugging flags?)' % e)
        return 1

    try:
        if cmd == 'courses':
            data = L.out(lms.courses())
        elif cmd == 'open':
            data = L.out(lms.open_course(args[1]))
        elif cmd == 'map':
            data = L.out(lms.map_modules(args[1] if len(args) > 1 else None))
        elif cmd == 'context':
            data = L.out(lms.build_context(args[1], outdir=(args[2] if len(args) > 2 else None)))
        elif cmd == 'assign':
            outdir = args[2] if len(args) > 2 else os.path.join(L.CTX_ROOT, 'assignments', args[1])
            data = L.out(lms.download_assign(args[1], outdir))
            if data.get('text'):
                tp = os.path.join(outdir, 'page_text.json')
                os.makedirs(outdir, exist_ok=True)
                with open(tp, 'w', encoding='utf-8') as f:
                    json.dump({'text': data['text'], 'url': data.get('url')}, f, ensure_ascii=False, indent=1)
                data['text_saved'] = tp
        elif cmd == 'quiz':
            data = L.out(lms.quiz_status(args[1]))
        elif cmd == 'start':
            data = L.out(lms.start_attempt(args[1]))
        elif cmd == 'collect':
            data = lms.collect()
            L.out(data)
            save_json('questions.json', data)
            data = dict(data)
            data['_saved'] = 'questions.json'
        elif cmd == 'fill':
            src = args[1]
            if src.startswith('{'):
                answers = json.loads(src)
            else:
                p = src if os.path.isabs(src) else os.path.join(LIBDIR, src)
                with open(p, encoding='utf-8') as f:
                    answers = json.load(f)
            wrapped = answers if ('select' in answers or 'text' in answers) else {'select': answers}
            data = L.out(lms.fill(wrapped))
        elif cmd == 'submit':
            data = L.out(lms.submit_attempt())
        elif cmd == 'result':
            data = L.out(lms.run_result())
        elif cmd == 'review':
            data = L.out(lms.review())
        elif cmd == 'search':
            data = L.out(lms.web_search(' '.join(args[1:])))
        elif cmd == 'read':
            data = L.out(lms.read_url(args[1]))
        elif cmd == 'tabs':
            data = L.out({'handles': lms.m.handles(), 'url': lms.url()})
        elif cmd == 'close-search':
            data = L.out({'closed': lms.close_search_tab()})
        else:
            print('unknown command:', cmd)
            print(__doc__)
            return 2
        print('OK:', brief(data))
        return 0
    except Exception as e:
        L.out({'FATAL': repr(e)})
        print('ERROR:', repr(e), '-> harness_report.txt')
        return 1


if __name__ == '__main__':
    sys.exit(main())