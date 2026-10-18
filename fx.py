# -*- coding: utf-8 -*-
"""Marionette client — remote control of Firefox. Pure stdlib.
Wire protocol v3: length-prefixed JSON arrays [0, msgId, cmd, params] / [1, msgId, error, result].

Usage: python fx.py <command> [args]
Commands:
  demo              - connect, session, url/title/tabs
  probe             - check which Marionette commands exist in this build
  url / title       - current tab url / title
  goto <url>        - navigate current tab
  open <url>        - open new tab and switch to it
  tabs              - list all tabs
  activate <n>      - switch to tab n (0-based)
  close <n>         - close tab n
  eval "<js>"       - run JS in page, return JSON
  click <css>       - click first element matching CSS selector
  shot [full]       - screenshot -> fx_screenshot.png
  src               - page source -> fx_page.html
All results are also logged to fx_report.txt (utf-8).
"""
import base64, json, os, socket, sys, time

HOST, PORT = '127.0.0.1', 2828
ROOT = os.path.dirname(os.path.abspath(__file__))
TMPDIR = os.path.join(ROOT, 'tmp')            # throw-away artifacts live here
os.makedirs(TMPDIR, exist_ok=True)
REPORT = os.path.join(TMPDIR, 'fx_report.txt')
SESSION_FILE = os.path.join(TMPDIR, 'fx_session.txt')
STATE_FILE = os.path.join(TMPDIR, 'fx_state.json')
SHOT_FILE = os.path.join(TMPDIR, 'fx_screenshot.png')
PAGE_FILE = os.path.join(TMPDIR, 'fx_page.html')

LOG = []


def log(s=''):
    LOG.append(str(s))


def flush():
    with open(REPORT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG))


def deep_find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = deep_find(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = deep_find(v, key)
            if r is not None:
                return r
    return None


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    with open(STATE_FILE, 'w') as f:
        json.dump(st, f)


class Marionette:
    def __init__(self, host=HOST, port=PORT, timeout=60):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.f = self.sock.makefile('rb')
        self.handshake = self._recv_json()
        self.msgid = 0
        self.session_id = None

    def _recv_json(self):
        buf = b''
        while True:
            ch = self.f.read(1)
            if not ch:
                raise EOFError('connection closed')
            if ch == b':':
                break
            buf += ch
        return json.loads(self.f.read(int(buf)).decode('utf-8'))

    def cmd(self, name, params=None):
        """Send [0, id, name, params]; wait for [1, id, error, result]."""
        self.msgid += 1
        msg = [0, self.msgid, name, params if params is not None else {}]
        data = json.dumps(msg, ensure_ascii=False).encode('utf-8')
        self.sock.sendall(str(len(data)).encode() + b':' + data)
        while True:
            resp = self._recv_json()
            if isinstance(resp, list) and len(resp) == 4 and resp[0] == 1:
                if resp[1] == self.msgid:
                    _, _, error, result = resp
                    return error, result
                continue
            return None, {'__unexpected__': resp}


def get(m, name, params=None):
    """WebDriver command -> unwrapped 'value'."""
    error, result = m.cmd(name, params)
    if error is not None:
        return {'__error__': error}
    if isinstance(result, dict) and 'value' in result:
        return result['value']
    return result


def js(m, script):
    """Execute JS in current page context, return value."""
    error, result = m.cmd('WebDriver:ExecuteScript', {'script': script, 'args': []})
    if error is not None:
        return {'__error__': error}
    if isinstance(result, dict) and 'value' in result:
        return result['value']
    return result


def ensure_session(m):
    for params in ({'capabilities': {'alwaysMatch': {}}}, {}):
        error, result = m.cmd('WebDriver:NewSession', params)
        if error is None:
            sid = deep_find(result, 'sessionId')
            if sid and isinstance(sid, str) and len(sid) < 100:
                m.session_id = sid
                with open(SESSION_FILE, 'w') as f:
                    f.write(sid)
                log(f'[session] created {sid}')
            else:
                log('[session] created (no explicit id)')
            return m.session_id
        etxt = json.dumps(error).lower()
        if 'already' in etxt or 'session' in etxt:
            log('[session] reusing existing session')
            return m.session_id
        log(f'[session] error: {json.dumps(error, ensure_ascii=False)[:200]}')
    raise RuntimeError('could not start session')


def current_url(m):
    v = js(m, 'return location.href')
    return v


def get_handles(m):
    h = get(m, 'WebDriver:GetWindowHandles')
    return h if isinstance(h, list) else None


def switch_to(m, handle):
    state = load_state()
    state['current_handle'] = handle
    save_state(state)
    return m.cmd('WebDriver:SwitchToWindow', {'handle': handle})


def tabs_info(m):
    handles = get_handles(m)
    if not isinstance(handles, list):
        return {'__error__': 'no window handles'}
    cur = load_state().get('current_handle') or handles[0]
    infos = []
    for i, h in enumerate(handles):
        m.cmd('WebDriver:SwitchToWindow', {'handle': h})
        info = js(m, 'return [document.title, location.href]')
        title, url = '', ''
        if isinstance(info, list) and len(info) == 2:
            title, url = info[0], info[1]
        elif not (isinstance(info, dict) and '__error__' in info):
            title, url = str(info), ''
        infos.append({'i': i, 'handle': h, 'url': url, 'title': title,
                      'current': h == cur})
    if cur in handles:
        m.cmd('WebDriver:SwitchToWindow', {'handle': cur})
    else:
        state = load_state()
        state['current_handle'] = handles[0]
        save_state(state)
        m.cmd('WebDriver:SwitchToWindow', {'handle': handles[0]})
    return infos


def goto(m, url):
    err, _ = m.cmd('WebDriver:Navigate', {'url': url})
    if err is not None and err.get('error') == 'unknown command':
        js(m, 'location.href = ' + json.dumps(url))
        time.sleep(2.0)
        return current_url(m)
    return current_url(m)


def open_tab(m, url):
    before = set(get_handles(m) or [])
    js(m, 'window.open(' + json.dumps(url) + ', "_blank"); return "ok"')
    time.sleep(1.5)
    handles = get_handles(m) or []
    new = [h for h in handles if h not in before]
    if new:
        switch_to(m, new[-1])
        return {'opened': True, 'new_handle': new[-1], 'handle_count': len(handles)}
    return {'opened': False, 'note': 'new window was blocked; handles unchanged'}


def close_tab(m, idx):
    handles = get_handles(m) or []
    if not isinstance(idx, int) or idx < 0 or idx >= len(handles):
        return {'__error__': f'index out of range 0..{len(handles) - 1}'}
    target = handles[idx]
    m.cmd('WebDriver:SwitchToWindow', {'handle': target})
    err, _ = m.cmd('WebDriver:CloseWindow', {})
    if err is not None and err.get('error') == 'unknown command':
        js(m, 'window.close(); return "closed"')
        time.sleep(0.8)
    after = get_handles(m) or []
    state = load_state()
    if target in after:
        return {'closed': False, 'handles': after}
    stay = state.get('current_handle')
    if stay in after:
        m.cmd('WebDriver:SwitchToWindow', {'handle': stay})
    elif after:
        state['current_handle'] = after[0]
        save_state(state)
        m.cmd('WebDriver:SwitchToWindow', {'handle': after[0]})
    return {'closed': True, 'handles': after}


def probe(m):
    log('=== Probe: available Marionette commands ===')
    checks = [
        ('WebDriver:GetTitle', {}),
        ('WebDriver:GetCurrentUrl', {}),
        ('WebDriver:GetWindowHandles', {}),
        ('WebDriver:Navigate', {'url': 'about:blank'}),
        ('WebDriver:ExecuteScript', {'script': 'return 1+1', 'args': []}),
        ('WebDriver:TakeScreenshot', {'full': False}),
        ('WebDriver:GetPageSource', {}),
        ('WebDriver:CloseWindow', {}),
        ('WebDriver:SwitchToWindow', {'handle': 'probe-invalid'}),
    ]
    for name, params in checks:
        error, result = m.cmd(name, params)
        if error is not None and error.get('error') == 'unknown command':
            log(f'{name}: MISSING')
        elif error is not None:
            log(f'{name}: EXISTS (error: {error.get("error", "?")})')
        else:
            log(f'{name}: OK -> {json.dumps(result, ensure_ascii=False)[:120]}')


def screenshot(m, full=False):
    error, result = m.cmd('WebDriver:TakeScreenshot', {'full': bool(full)})
    if error is not None:
        return None, {'__error__': error}
    b64 = result if isinstance(result, str) else deep_find(result, 'value')
    if not isinstance(b64, str) or len(b64) < 100:
        return None, {'__error__': 'no image in response'}
    with open(SHOT_FILE, 'wb') as f:
        f.write(base64.b64decode(b64))
    return SHOT_FILE, None


def run_demo():
    m = Marionette()
    log('=== Marionette: connected ===')
    log('handshake: ' + json.dumps(m.handshake, ensure_ascii=False))
    ensure_session(m)
    log('')
    log('=== Current tab ===')
    time.sleep(1.5)
    log('url: ' + json.dumps(current_url(m), ensure_ascii=False))
    log('title: ' + json.dumps(get(m, 'WebDriver:GetTitle'), ensure_ascii=False))
    log('')
    log('=== All tabs ===')
    tabs = tabs_info(m)
    log(json.dumps(tabs, ensure_ascii=False, indent=2))


def main():
    args = sys.argv[1:]
    try:
        if not args or args[0] == 'demo':
            run_demo()
            flush()
            print('OK demo -> see fx_report.txt')
            return
        m = Marionette()
        ensure_session(m)
        c = args[0]
        if c == 'probe':
            probe(m)
        elif c == 'url':
            log(json.dumps(current_url(m), ensure_ascii=False))
        elif c == 'title':
            log(json.dumps(get(m, 'WebDriver:GetTitle'), ensure_ascii=False))
        elif c == 'goto' and len(args) > 1:
            log(json.dumps(goto(m, args[1]), ensure_ascii=False))
        elif c == 'open' and len(args) > 1:
            log(json.dumps(open_tab(m, args[1]), ensure_ascii=False))
        elif c == 'tabs':
            log(json.dumps(tabs_info(m), ensure_ascii=False, indent=2))
        elif c == 'activate' and len(args) > 1:
            handles = get_handles(m) or []
            n = int(args[1])
            err, _ = switch_to(m, handles[n])
            log(json.dumps({'activated': n, 'url': current_url(m)} if err is None
                           else {'__error__': err}, ensure_ascii=False))
        elif c == 'close' and len(args) > 1:
            log(json.dumps(close_tab(m, int(args[1])), ensure_ascii=False))
        elif c == 'eval' and len(args) > 1:
            log(json.dumps(js(m, args[1]), ensure_ascii=False))
        elif c == 'evalfile' and len(args) > 1:
            with open(args[1], 'r', encoding='utf-8') as f:
                script = f.read()
            log(json.dumps(js(m, script), ensure_ascii=False))
        elif c == 'click' and len(args) > 1:
            res = js(m, 'var el=document.querySelector(' + json.dumps(args[1]) +
                     '); if(el){el.click(); return "clicked"} return "not found"')
            log(json.dumps(res, ensure_ascii=False))
        elif c == 'shot':
            full = len(args) > 1 and args[1] == 'full'
            path, error = screenshot(m, full)
            log('saved: ' + path if path else 'failed: ' + json.dumps(error, ensure_ascii=False)[:300])
        elif c == 'src':
            v = get(m, 'WebDriver:GetPageSource')
            if isinstance(v, str):
                with open(PAGE_FILE, 'w', encoding='utf-8') as f:
                    f.write(v)
                log(f'saved: {PAGE_FILE} ({len(v)} chars)')
            else:
                log(json.dumps(v, ensure_ascii=False)[:300])
        else:
            log('unknown command: ' + ' '.join(args))
        flush()
        print('OK -> see fx_report.txt')
    except Exception as e:
        log(f'FATAL: {e!r}')
        flush()
        print('ERROR -> see fx_report.txt')


if __name__ == '__main__':
    main()