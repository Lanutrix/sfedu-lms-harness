# -*- coding: utf-8 -*-
"""LMS harness library — control lms.sfedu.ru (Moodle) through Firefox/Marionette.

Layers:
  Marionette  - raw protocol client ([0,id,cmd,params] / [1,id,error,result])
  LMS         - high level: courses, modules, materials download, quiz engine,
                web search in a SEPARATE tab (quiz tab never leaves the LMS).

State files (same dir):
  fx_session.txt      - marionette session id
  harness_report.txt  - result of the last operation (utf-8)
  lms_state.json      - search-tab handle etc.

Requires Firefox launched with -marionette (port 2828).
"""
import base64, glob, json, os, re, socket, sqlite3, subprocess, time, shutil, urllib.parse, urllib.request

HOST = '127.0.0.1'
PORT = 2828
ROOT = 'https://lms.sfedu.ru'
DL_DIR = os.path.join(os.path.expanduser('~'), 'Downloads')
LIBDIR = os.path.dirname(os.path.abspath(__file__))
CTX_ROOT = os.path.join(LIBDIR, 'context')
TMPDIR = os.path.join(LIBDIR, 'tmp')          # ALL throw-away artifacts live here
os.makedirs(TMPDIR, exist_ok=True)
REPORT = os.path.join(TMPDIR, 'harness_report.txt')
SESSION_FILE = os.path.join(TMPDIR, 'fx_session.txt')
STATE_FILE = os.path.join(TMPDIR, 'lms_state.json')
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


class EngineError(Exception):
    pass


class MarionetteError(EngineError):
    pass


class Marionette:
    def __init__(self, timeout=60):
        self.sock = socket.create_connection((HOST, PORT), timeout=timeout)
        self.f = self.sock.makefile('rb')
        self.handshake = self._recv_json()
        if 'marionetteProtocol' not in self.handshake:
            raise MarionetteError('unexpected handshake: %r' % self.handshake)
        self.msgid = 0

    def _recv_json(self):
        buf = b''
        while True:
            ch = self.f.read(1)
            if not ch:
                raise EOFError('marionette connection closed')
            if ch == b':':
                break
            buf += ch
        return json.loads(self.f.read(int(buf)).decode('utf-8'))

    def cmd(self, name, params=None):
        self.msgid += 1
        msg = [0, self.msgid, name, params if params is not None else {}]
        data = json.dumps(msg, ensure_ascii=False).encode('utf-8')
        self.sock.sendall(str(len(data)).encode() + b':' + data)
        while True:
            resp = self._recv_json()
            if isinstance(resp, list) and len(resp) == 4 and resp[0] == 1 and resp[1] == self.msgid:
                _, _, error, result = resp
                if error is not None:
                    raise MarionetteError(json.dumps(error, ensure_ascii=False)[:400])
                return result

    # --- helpers ---
    def js(self, script):
        r = self.cmd('WebDriver:ExecuteScript', {'script': script, 'args': []})
        if isinstance(r, dict) and 'value' in r:
            return r['value']
        return r

    def navigate(self, url):
        try:
            self.cmd('WebDriver:Navigate', {'url': url})
        except MarionetteError:
            # command missing or download-ish navigation timing out
            self.js('location.href = %s;' % json.dumps(url))
            time.sleep(1.5)
        return self.current_url()

    def current_url(self):
        return self.js('return location.href')

    def title(self):
        return self.js('return document.title')

    def handles(self):
        h = self.cmd('WebDriver:GetWindowHandles')
        return h if isinstance(h, list) else []

    def switch(self, handle):
        return self.cmd('WebDriver:SwitchToWindow', {'handle': handle})

    # --- compatibility layer (same surface as ChromeCDP) ---
    def get_value(self, name, params=None):
        try:
            try:
                r = self.cmd(name, params)
            except MarionetteError as e:
                return {'__error__': str(e)}
            if isinstance(r, dict) and 'value' in r:
                return r['value']
            return r
        except Exception as e:
            return {'__error__': repr(e)}

    def close_window(self):
        self.cmd('WebDriver:CloseWindow')
        return True


def connect():
    """Connect + ensure WebDriver session (reuse existing one)."""
    m = Marionette()
    for params in ({'capabilities': {'alwaysMatch': {}}}, {}):
        try:
            m.msgid += 1
            msg = [0, m.msgid, 'WebDriver:NewSession', params]
            data = json.dumps(msg, ensure_ascii=False).encode('utf-8')
            m.sock.sendall(str(len(data)).encode() + b':' + data)
            resp = m._recv_json()
            if isinstance(resp, list) and len(resp) == 4 and resp[0] == 1:
                _, _, err, res = resp
                if err is None:
                    return m
                # 'session already created' -> reuse silently
        except (EOFError, OSError):
            break
    return m


# ---------------------------------------------------------------------------
# Browser lifecycle management
# ---------------------------------------------------------------------------
FIREFOX_CANDIDATES = [
    r'C:\Program Files\Mozilla Firefox\firefox.exe',
    r'C:\Program Files (x86)\Mozilla Firefox\firefox.exe',
    os.path.expandvars(r'%LOCALAPPDATA%\Mozilla Firefox\firefox.exe'),
]

FIREFOX_MISSING_MSG = ('Mozilla Firefox не найден. Установи его с https://www.mozilla.org/ru/firefox/new/ '
                       'и повтори — без Firefox харнесс работать не может.')


def find_firefox():
    for p in FIREFOX_CANDIDATES:
        if os.path.isfile(p):
            return p
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, r'SOFTWARE\Mozilla\Mozilla Firefox\CurrentVersion\Main') as k:
                    path, _ = winreg.QueryValueEx(k, 'PathToExe')
                if path and os.path.isfile(path):
                    return path
            except OSError:
                continue
    except ImportError:
        pass
    return None


def _port_open(timeout=0.7):
    try:
        s = socket.create_connection((HOST, PORT), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _ff_running():
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq firefox.exe', '/NH'],
                             capture_output=True, text=True, timeout=10).stdout or ''
    except Exception:
        return False
    return 'firefox.exe' in out.lower()


def ensure_browser(timeout=45):
    """Guarantee Firefox runs WITH -marionette.

    Returns {'status': 'ok'|'launched'|'restarted'|'not_installed'|'timeout', ...}
    Never closes Firefox if the marionette port already works.
    """
    if _port_open():
        return {'status': 'ok'}
    exe = find_firefox()
    if not exe:
        return {'status': 'not_installed', 'message': FIREFOX_MISSING_MSG}
    was_running = _ff_running()
    if was_running:
        subprocess.run(['taskkill', '/IM', 'firefox.exe'], capture_output=True)
        for _ in range(24):                      # graceful close, up to ~12s
            if not _ff_running():
                break
            time.sleep(0.5)
        if _ff_running():                       # still alive -> force
            subprocess.run(['taskkill', '/F', '/IM', 'firefox.exe'], capture_output=True)
            for _ in range(20):
                if not _ff_running():
                    break
                time.sleep(0.5)
    subprocess.Popen([exe, '-marionette'], close_fds=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.8)
        if _port_open():
            return {'status': 'restarted' if was_running else 'launched'}
    return {'status': 'timeout',
            'message': 'Firefox запущен, но порт управления 2828 не открылся за %d с' % timeout}


# ---------------------------------------------------------------------------
# Chrome/Chromium engine (CDP over localhost:9222)
# ---------------------------------------------------------------------------
CHROME_CANDIDATES = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
]
EDGE_CANDIDATES = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]
CHROME_PROFILE = os.path.join(LIBDIR, 'chrome_profile')
CHROME_MISSING_MSG = ('Chrome не найден. Установи Google Chrome с https://www.google.com/chrome/ '
                      '(или используй движок Firefox, который уже настроен).')
CDP_PORT = 9222


def find_chromium():
    for p in CHROME_CANDIDATES + EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    try:
        import winreg
        for name in ('chrome.exe', 'msedge.exe'):
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(root,
                            r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s' % name) as k:
                        path = winreg.QueryValueEx(k, '')[0]
                    if path and os.path.isfile(path):
                        return path
                except OSError:
                    continue
    except ImportError:
        pass
    return None


def _cdp_alive(port=CDP_PORT, timeout=1.2):
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/json/version' % port, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_browser_chrome(port=CDP_PORT, timeout=40):
    """Launch Chrome/Edge with CDP. Uses a SEPARATE profile dir (chrome_profile)
    so your daily browser session is never touched -> no restarts, no conflicts."""
    if _cdp_alive(port):
        return {'status': 'ok'}
    exe = find_chromium()
    if not exe:
        return {'status': 'not_installed', 'message': CHROME_MISSING_MSG}
    subprocess.Popen(
        [exe, '--remote-debugging-port=%d' % port,
         '--user-data-dir=%s' % CHROME_PROFILE,
         '--no-first-run', '--no-default-browser-check',
         'about:blank'],
        close_fds=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.8)
        if _cdp_alive(port):
            return {'status': 'launched', 'exe': exe}
    return {'status': 'timeout',
            'message': 'Chrome запущен, но CDP-порт %d не открылся за %d с' % (port, timeout)}


class WSClient:
    """Minimal websocket client (stdlib only) for CDP connections."""

    def __init__(self, host, port, path, timeout=60):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ('GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n'
               'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n'
               'Sec-WebSocket-Version: 13\r\n\r\n') % (path, host, port, key)
        self.sock.sendall(req.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise EngineError('ws handshake closed')
            buf += chunk
        if b'101' not in buf.split(b'\r\n')[0]:
            raise EngineError('ws handshake failed: %r' % buf[:100])
        self.buf = buf.split(b'\r\n\r\n', 1)[1]

    def _read_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError('ws closed')
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send_text(self, text):
        data = text.encode('utf-8')
        mask = os.urandom(4)
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += n.to_bytes(2, 'big')
        else:
            header.append(0x80 | 127)
            header += n.to_bytes(8, 'big')
        header += mask
        self.sock.sendall(bytes(header) +
                          bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv_text(self):
        acc = b''
        while True:
            h = self._read_exact(2)
            fin, op = h[0] & 0x80, h[0] & 0x0F
            ln = h[1] & 0x7F
            if ln == 126:
                ln = int.from_bytes(self._read_exact(2), 'big')
            elif ln == 127:
                ln = int.from_bytes(self._read_exact(8), 'big')
            payload = self._read_exact(ln)
            if op == 0x9:  # ping -> pong
                mask = os.urandom(4)
                hdr = bytearray([0x8A]); hdr.append(0x80 | len(payload)); hdr += mask
                self.sock.sendall(bytes(hdr) +
                                  bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
                continue
            if op == 0x8:
                raise EOFError('ws close frame')
            if op in (0, 1, 2):
                acc += payload
            if fin:
                return acc.decode('utf-8', 'replace')

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class ChromeCDP:
    """CDP engine implementing the same surface as the Marionette class."""

    def __init__(self, port=CDP_PORT, timeout=60):
        self.port, self.timeout = port, timeout
        self.msgid = 0
        self.ws = None
        self.target = None
        self._attach_any_page()

    def _http(self, path, method='GET'):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, path), method=method)
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode('utf-8', 'replace')
        try:
            return json.loads(raw)
        except ValueError:
            return {'raw': raw}  # /json/activate, /json/close return plain text

    def _pages(self):
        try:
            return [t for t in self._http('/json/list') if t.get('type') == 'page']
        except Exception:
            return []

    def _attach(self, target):
        wsurl = target.get('webSocketDebuggerUrl')
        if not wsurl:
            raise EngineError('target without ws url')
        m = re.match(r'ws://([^:/]+):(\d+)(/.+)', wsurl)
        if not m:
            raise EngineError('bad ws url %s' % wsurl)
        if self.ws:
            self.ws.close()
        self.target = target
        self.ws = WSClient(m.group(1), int(m.group(2)), m.group(3), self.timeout)

    def _attach_any_page(self):
        pages = self._pages()
        if not pages:
            raise EngineError('нет открытых вкладок Chrome (CDP)')
        self._attach(pages[0])

    def command(self, method, params=None):
        self.msgid += 1
        self.ws.send_text(json.dumps(
            {'id': self.msgid, 'method': method, 'params': params or {}},
            ensure_ascii=False))
        while True:
            msg = json.loads(self.ws.recv_text())
            if msg.get('id') == self.msgid:
                if 'error' in msg:
                    raise EngineError(json.dumps(msg['error'])[:300])
                return msg.get('result', {})
            # events / foreign replies -> skip

    def js(self, script):
        # harness scripts are function bodies (may contain 'return') or IIFEs;
        # wrapping always keeps both styles valid in CDP evaluation
        expr = '(function(){ %s })()' % script if 'return' in script else script
        res = self.command('Runtime.evaluate',
                           {'expression': expr, 'returnByValue': True, 'awaitPromise': False})
        if res.get('exceptionDetails'):
            raise EngineError(json.dumps(res['exceptionDetails'], ensure_ascii=False)[:300])
        return (res.get('result') or {}).get('value')

    def navigate(self, url):
        try:
            self.command('Page.navigate', {'url': url})
            time.sleep(1.0)
        except EngineError:
            self.js('location.href = %s;' % json.dumps(url))
            time.sleep(1.2)
        return self.current_url() or ''

    def current_url(self):
        try:
            return self.js('return location.href') or ''
        except EngineError:
            return ''

    def title(self):
        try:
            return self.js('return document.title') or ''
        except EngineError:
            return ''

    def handles(self):
        return [t['id'] for t in self._pages()]

    def switch(self, handle):
        for t in self._pages():
            if t['id'] == handle:
                self._http('/json/activate/' + handle)
                self._attach(t)
                return True
        return False

    def close_window(self):
        if self.target:
            try:
                self._http('/json/close/' + self.target['id'])
                return True
            except Exception:
                return False
        return False

    def set_cookie(self, name, value, domain, path='/', secure=False,
                   http_only=False, expires=None):
        p = {'name': name, 'value': value, 'domain': domain, 'path': path,
             'secure': bool(secure), 'httpOnly': bool(http_only)}
        if expires:
            p['expires'] = int(expires)
        return bool(self.command('Network.setCookie', p))


def firefox_cookies_for(host_key='sfedu.ru'):
    """Read cookies for a host straight from Firefox's cookies.sqlite (best effort)."""
    prof_root = os.path.join(os.environ.get('APPDATA', ''), 'Mozilla', 'Firefox', 'Profiles')
    best = None
    for p in glob.glob(os.path.join(prof_root, '*', 'cookies.sqlite')):
        if best is None or os.path.getmtime(p) > os.path.getmtime(best):
            best = p
    if not best:
        return []
    rows = []
    for immutable in (True, False):
        try:
            uri = 'file:%s?immutable=1' % best.replace('?', '%3f') if immutable else best
            con = sqlite3.connect(uri, uri=immutable, timeout=2)
            cur = con.execute(
                'SELECT host, name, value, path, isSecure, isHttpOnly, expiry '
                'FROM moz_cookies WHERE host LIKE ?', ('%' + host_key,))
            rows = cur.fetchall()
            con.close()
            break
        except Exception:
            continue
    return rows


def ensure_connection(engine=None, max_retry=2):
    engine = (engine or os.environ.get('LMS_ENGINE', 'firefox') or 'firefox').strip().lower()
    if engine in ('chrome', 'chromium', 'edge', 'msedge'):
        for _ in range(max_retry):
            st = ensure_browser_chrome()
            if st.get('status') in ('ok', 'launched'):
                try:
                    return ChromeCDP()
                except Exception:
                    time.sleep(1.5)
                    continue
            if st.get('status') == 'not_installed':
                raise RuntimeError(st.get('message', CHROME_MISSING_MSG))
            time.sleep(2.0)
        raise RuntimeError('Chrome запущен, но CDP-подключение (порт %d) не удалось' % CDP_PORT)
    # firefox/marionette branch
    for attempt in range(max_retry):
        try:
            return connect()
        except (ConnectionRefusedError, ConnectionResetError, OSError):
            pass
        st = ensure_browser()
        if st.get('status') == 'not_installed':
            raise RuntimeError(st.get('message', FIREFOX_MISSING_MSG))
        if st.get('status') == 'timeout':
            raise RuntimeError(st.get('message', 'marionette port timeout'))
    # last try
    try:
        return connect()
    except OSError as e:
        raise RuntimeError('Не удалось подключиться к Firefox (порт 2828): %r' % e)


def ensure_no_overload(f):
    return f


# ============================================================================
# JS snippets
# ============================================================================

JS_COURSES = """
return (function(){
  var seen = {}; var out = [];
  document.querySelectorAll('a[href*="course/view.php"]').forEach(function(a){
    var m = (a.href||'').match(/id=(\\d+)/);
    var t = a.textContent.trim().replace(/\\s+/g,' ').substring(0,120);
    if (!m || !t) return;
    if (seen[m[1]]) { return; }
    seen[m[1]] = 1; out.push({id: m[1], name: t});
  });
  return out;
})();
"""

JS_MODULES = """
return (function(){
  var out = [];
  document.querySelectorAll('li.activity').forEach(function(li){
    var a = li.querySelector('a[href*="/mod/"]') || li.querySelector('a[href*="/course/"]');
    if (!a) return;
    var href = a.href || '';
    var m = href.match(/mod\\/(\\w+)\\/view\\.php\\?id=(\\d+)/);
    var t = (a.textContent||'').trim().replace(/\\s+/g,' ').substring(0,140);
    if (!t) {
      var inst = li.textContent.trim().replace(/\\s+/g,' ');
      t = inst.substring(0,140);
    }
    var sec = li.closest('li.section, .course-section');
    var secName = '';
    if (sec) {
      var h = sec.querySelector('.sectionname, h3.sectionname');
      if (h) secName = h.textContent.trim().replace(/\\s+/g,' ').substring(0,80);
    }
    out.push({type: m ? m[1] : '?', id: m ? m[2] : '?', name: t, href: href, section: secName});
  });
  return out;
})();
"""

JS_DOWNLOAD = """
return (function(){
  var url = location.href.split('?')[0] + '?forcedownload=1';
  var a = document.createElement('a');
  a.href = url; a.download = '';
  document.body.appendChild(a); a.click();
  return 'download started: ' + url;
})();
"""

JS_PLUGINFILES = """
return Array.from(document.querySelectorAll('a[href*="pluginfile"]')).map(function(a){
  return {href: a.href, text: (a.textContent||'').trim().replace(/\\s+/g,' ').substring(0,140)};
});
"""

JS_PAGETEXT = """
return document.body.innerText.replace(/\\s+/g, ' ');
"""

JS_MAINTEXT = """
var el = document.querySelector('#region-main, .region-content, [role="main"]') || document.body;
return el.innerText.replace(/\\s+/g, ' ');
"""

JS_QUIZ_STATE = """
return (function(){
  function txt(x){ return (x.value||x.textContent||'').replace(/\\s+/g,' ').trim(); }
  return {
    url: location.href,
    title: document.title,
    text: document.body.innerText.replace(/\\s+/g,' ').substring(0, 1600),
    forms: Array.from(document.querySelectorAll('form')).map(function(f){
      return {action: f.action, buttons: Array.from(f.querySelectorAll('button,input[type=submit]')).map(txt).filter(Boolean)};
    }),
    tables: Array.from(document.querySelectorAll('table.generaltable')).map(function(t){
      return Array.from(t.querySelectorAll('tr')).map(function(tr){
        return Array.from(tr.querySelectorAll('th,td')).map(function(c){
          return c.textContent.replace(/\\s+/g,' ').trim();
        }).filter(Boolean).join(' || ');
      }).filter(Boolean);
    })
  };
})();
"""

JS_START_ATTEMPT = """
return (function(){
  function txt(x){ return (x.value||x.textContent||'').replace(/\\s+/g,' ').trim(); }
  var all = Array.from(document.querySelectorAll('button, input[type=submit], a.btn'));
  var b2 = all.find(function(x){ return txt(x) === 'Начать попытку'; });
  if (b2) { b2.click(); return 'modal-start-clicked'; }
  var b1 = all.find(function(x){ return /^Пройти тест/.test(txt(x)); });
  if (b1) { b1.click(); return 'start-clicked'; }
  return 'none';
})();
"""

JS_FINISH = """
return (function(){
  function txt(x){ return (x.value||x.textContent||'').replace(/\\s+/g,' ').trim(); }
  var bs = Array.from(document.querySelectorAll('button, input[type=submit]'));
  var b = bs.find(function(x){ return /^Закончить попытку/.test(txt(x)); });
  if (b) { b.click(); return 'finish-clicked'; }
  return 'no-finish-button';
})();
"""

JS_CONTINUE = """
return (function(){
  function txt(x){ return (x.value||x.textContent||'').replace(/\\s+/g,' ').trim(); }
  var all = Array.from(document.querySelectorAll('button, input[type=submit], a.btn'));
  var b = all.find(function(x){ return /Продолжить текущую попытку/.test(txt(x)); });
  if (b) { b.click(); return 'continue-clicked'; }
  return 'none';
})();
"""

JS_FORCE_SUBMIT = """
return (function(){
  var f = Array.from(document.querySelectorAll('form')).find(function(x){
    return /processattempt/.test(x.action);
  });
  if (!f) return 'no-processattempt-form';
  f.submit();
  return 'submitted';
})();
"""

def _fill_js(answers):
    """answers: {'select': {name: exact_text|substring_text}, 'text': {name: value}}
    keys in 'select' starting with '~' on the VALUE side are substring matches."""
    return """
return (function(){
  var spec = %s;
  function norm(s){ return s.replace(/\\s+/g, ' ').trim(); }
  var bad = [];
  Object.keys(spec.select || {}).forEach(function(name){
    var el = document.querySelector('select[name="' + name + '"]');
    if (!el) { bad.push(name + ':FIELD_NOT_FOUND'); return; }
    var want = spec.select[name];
    var hit = null;
    Array.from(el.options).forEach(function(o){
      if (hit) return;
      var t = norm(o.textContent);
      if (t.toLowerCase() === String(want).toLowerCase()) hit = o.value;
    });
    if (hit === null) {
      Array.from(el.options).forEach(function(o){
        if (hit || !o.value) return;
        var t = norm(o.textContent);
        if (t.toLowerCase().indexOf(String(want).toLowerCase()) !== -1) hit = o.value;
      });
    }
    if (hit === null) { bad.push(name + ':OPTION_NOT_FOUND:' + want); return; }
    el.value = hit;
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  });
  Object.keys(spec.text || {}).forEach(function(name){
    var el = document.querySelector('input[name="' + name + '"], textarea[name="' + name + '"]');
    if (!el) { bad.push(name + ':FIELD_NOT_FOUND'); return; }
    el.value = spec.text[name];
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  });
  return {bad: bad, selected: Array.from(document.querySelectorAll('div.que select')).map(function(sel){
    return {name: sel.name, chosen: (sel.selectedIndex>=0 && sel.options[sel.selectedIndex].value)
      ? norm(sel.options[sel.selectedIndex].textContent).substring(0,60) : '(empty)'};
  }), texts: Array.from(document.querySelectorAll('div.que input[type=text], div.que textarea')).map(function(i){
    return {name: i.name, value: (i.value||'').substring(0,60)};
  })};
})();
""" % json.dumps(answers, ensure_ascii=False)

JS_COLLECT = """
return (function(){
  function norm(s){ return s.replace(/\\s+/g, ' '); }
  function collectSeq(root){
    var out = [];
    (function walk(node){
      Array.from(node.childNodes).forEach(function(ch){
        if (ch.nodeType === 3) out.push({t:'text', s: ch.textContent});
        else if (ch.tagName === 'SELECT') out.push({t:'sel', el: ch});
        else if (ch.tagName === 'INPUT' || ch.tagName === 'TEXTAREA') out.push({t:'inp', el: ch});
        else walk(ch);
      });
    })(root);
    return out;
  }
  var qes = Array.from(document.querySelectorAll('div.que'));
  var questions = qes.map(function(q, qi){
    var seq = collectSeq(q.querySelector('.formulation') || q);
    var prev = '';
    var fields = [];
    seq.forEach(function(it){
      if (it.t === 'text') { prev = norm(prev + it.s); return; }
      var idx = prev.lastIndexOf(' [X] ');
      var seg = (idx >= 0 ? prev.substring(idx + 5) : prev).trim();
      var f = {name: it.el.name, prev: seg.substring(seg.length - 220)};
      if (it.t === 'sel') {
        f.kind = 'select';
        f.options = Array.from(it.el.options).filter(function(o){return o.value;})
          .map(function(o){ return norm(o.textContent).trim(); });
      } else {
        f.kind = 'text';
      }
      fields.push(f);
      prev = prev + ' [X] ';
    });
    return {n: qi + 1, full: norm(q.innerText), fields: fields};
  });
  return {url: location.href, title: document.title, questionCount: qes.length, questions: questions};
})();
"""

JS_REVIEW = """
return (function(){
  var qes = Array.from(document.querySelectorAll('div.que'));
  return {
    url: location.href,
    summary: document.body.innerText.replace(/\\s+/g,' ').substring(0, 1500),
    questions: qes.map(function(q, i){
      var g = q.querySelector('.grade');
      return {n: i+1, grade: g ? g.textContent.trim().replace(/\\s+/g,' ') : ''};
    })
  };
})();
"""

JS_SEARCH_RESULTS = """
return (function(){
  var out = [];
  var links = document.querySelectorAll('a.result-link, a.result-link');
  if (links.length === 0) links = document.querySelectorAll('a[href*="http"]');
  var snips = document.querySelectorAll('td.result-snippet');
  var items = [];
  if (links.length > 0 && snips.length >= links.length) {
    links.forEach(function(a, i){
      items.push({title: a.textContent.trim().replace(/\\s+/g,' '), url: a.href,
                  snippet: snips[i] ? snips[i].textContent.trim().replace(/\\s+/g,' ').substring(0,300) : ''});
    });
  } else {
    document.querySelectorAll('a').forEach(function(a){
      var t = a.textContent.trim().replace(/\\s+/g,' ');
      if (t.length > 15 && /^https?:/.test(a.href) && a.href.indexOf('duckduckgo') === -1 && items.length < 15) {
        items.push({title: t.substring(0,140), url: a.href, snippet: ''});
      }
    });
  }
  return items.slice(0, 12);
})();
"""


# ============================================================================
# LMS high level
# ============================================================================

class LMS:
    def __init__(self, engine=None):
        # auto-starts the browser with the right flag when needed;
        # engine: 'firefox' (default) or 'chrome' (also env LMS_ENGINE)
        engine = (engine or os.environ.get('LMS_ENGINE', 'firefox') or 'firefox').strip().lower()
        self.engine = 'chrome' if engine in ('chrome', 'chromium', 'edge', 'msedge') else 'firefox'
        self.m = ensure_connection(engine=self.engine)

    # ---------- chrome: reuse the logged-in LMS session ----------
    def sync_firefox_cookies(self):
        """Best-effort: carry lms.sfedu.ru cookies into the chrome CDP profile.
        Source #1: live Firefox via marionette (WebDriver:GetCookies).
        Source #2 (fallback): Firefox cookies.sqlite (works on older builds)."""
        if not isinstance(self.m, ChromeCDP):
            return {'skipped': 'not a chrome engine'}
        cookies = None
        source = None
        try:
            ff = connect()
            try:
                ff.navigate(ROOT + '/my/')
                time.sleep(1.2)
                cookies = ff.cmd('WebDriver:GetCookies')
                source = 'marionette'
            finally:
                try:
                    ff.sock.close()
                except Exception:
                    pass
        except Exception as e:
            log('[cookies] live firefox not available: %r' % e)
        if not cookies:
            rows = firefox_cookies_for('sfedu.ru')
            cookies = [{'name': n, 'value': v, 'domain': h, 'path': p,
                        'secure': bool(s), 'httpOnly': bool(ho)}
                       for h, n, v, p, s, ho, _e in rows]
            source = 'sqlite'
        ok = 0
        for c in (cookies or [])[:80]:
            try:
                if self.m.set_cookie(c['name'], c['value'], c.get('domain', 'lms.sfedu.ru'),
                                    c.get('path') or '/', c.get('secure', False),
                                    c.get('httpOnly', False), None):
                    ok += 1
            except Exception:
                continue
        logged = False
        try:
            self.m.navigate(ROOT + '/my/')
            time.sleep(1.0)
            logged = 'login' not in (self.m.current_url() or '').lower()
        except Exception:
            pass
        return {'source': source, 'cookies_set': ok, 'logged_in': logged,
                'note': '' if logged else
                        'не удалось перенести сессию — открой окно Chrome и залогинься в LMS один раз, профиль сохранится'}

    # ---------- basics ----------
    def goto(self, url):
        self.m.navigate(url)
        return {'url': self.m.current_url(), 'title': self.m.title()}

    def url(self):
        return self.m.current_url()

    # ---------- courses ----------
    def courses(self):
        self.m.navigate(ROOT + '/my/')
        time.sleep(1.0)
        return self.m.js(JS_COURSES)

    def open_course(self, course_id):
        return self.goto(ROOT + '/course/view.php?id=%s' % course_id)

    def map_modules(self, course_id=None):
        if course_id:
            self.open_course(course_id)
            time.sleep(1.0)
        return self.m.js(JS_MODULES)

    # ---------- downloads ----------
    def _click_download(self, href):
        """Trigger a download WITHOUT navigating (anchor-click + forcedownload).
        Returns downloaded file path or None."""
        url = href.split('?')[0] + '?forcedownload=1'
        before = set(os.listdir(DL_DIR))
        before_sizes = {f: os.path.getsize(os.path.join(DL_DIR, f)) for f in before}
        spec = json.dumps(url, ensure_ascii=False)
        try:
            self.m.js("""
(function(){
  var url = %s;
  var a = document.createElement('a');
  a.href = url; a.download = '';
  document.body.appendChild(a); a.click();
  return 'click-ok';
})();
""" % spec)
        except Exception as e:
            log('[download] click failed: %r' % e)
            return None
        # wait for a NEW file (or a non-empty growing one) to appear
        stable = 0
        last = None
        for _ in range(20):
            time.sleep(1.0)
            new = [f for f in os.listdir(DL_DIR)
                   if f not in before or before_sizes.get(f) != os.path.getmtime(os.path.join(DL_DIR, f))]
            cand = []
            for f in new:
                if f.endswith(('.part', '.crdownload', '.tmp')):
                    continue
                full = os.path.join(DL_DIR, f)
                if os.path.isfile(full) and os.path.getsize(full) > 0:
                    cand.append(f)
                elif os.path.isfile(full) and time.time() - os.path.getmtime(full) > 5:
                    cand.append(f)  # allow small/empty files that stopped changing
            if cand:
                cand.sort(key=lambda f: os.path.getmtime(os.path.join(DL_DIR, f)), reverse=True)
                stable += 1
                last = os.path.join(DL_DIR, cand[0])
                if stable >= 2:
                    return last
            else:
                stable = 0
        return last

    def _suggested_name(self, href):
        return urllib.parse.unquote(href.split('/')[-1].split('?')[0])

    def _download_file(self, url):
        """Download file by clicking (no navigation). Kept for API compat."""
        return self._click_download(url)

    def _copy_into(self, src, outdir, suggested_name=None):
        os.makedirs(outdir, exist_ok=True)
        name = suggested_name or os.path.basename(src)
        name = re.sub(r'[\\/:*?"<>|]', '_', name)
        dst = os.path.join(outdir, name)
        i = 1
        while os.path.exists(dst):
            base, ext = os.path.splitext(name)
            dst = os.path.join(outdir, '%s_%d%s' % (base, i, ext))
            i += 1
        shutil.copy2(src, dst)
        return dst

    def download_resource(self, resource_id, outdir):
        """Open resource module (redirects to pluginfile viewer) then download
        via anchor-click (no navigation hang)."""
        self.m.navigate(ROOT + '/mod/resource/view.php?id=%s' % resource_id)
        time.sleep(1.5)
        cur = self.m.current_url()
        if 'pluginfile' not in cur:
            # maybe a page-like resource; try pluginfile links on it
            files = self.m.js(JS_PLUGINFILES)
            saved = []
            for f in (files or [])[:10]:
                path = self._click_download(f['href'])
                if path:
                    saved.append(self._copy_into(path, outdir, self._suggested_name(f['href'])))
            return {'url': cur, 'saved': saved}
        path = self._click_download(cur)
        if not path:
            return {'url': cur, 'saved': []}
        dst = self._copy_into(path, outdir, self._suggested_name(cur))
        return {'url': cur, 'saved': [dst]}

    def read_module_text(self, max_chars=18000):
        t = self.m.js(JS_MAINTEXT)
        return t[:max_chars] if isinstance(t, str) else ''

    def download_assign(self, assign_id, outdir):
        """Assignment: save intro text + all attached files (per-file fault tolerance)."""
        self.m.navigate(ROOT + '/mod/assign/view.php?id=%s' % assign_id)
        time.sleep(1.5)
        text = self.read_module_text()
        files = self.m.js(JS_PLUGINFILES) or []
        saved, errors = [], []
        for f in files[:20]:
            try:
                path = self._click_download(f['href'])
                if path:
                    saved.append(self._copy_into(path, outdir, self._suggested_name(f['href'])))
            except Exception as e:
                errors.append('%s: %r' % (f.get('text', '')[:40], e))
        return {'url': self.m.current_url(), 'text': text, 'files': files,
                'saved': saved, 'download_errors': errors}

    def download_folder(self, folder_id, outdir):
        self.m.navigate(ROOT + '/mod/folder/view.php?id=%s' % folder_id)
        time.sleep(1.5)
        files = self.m.js(JS_PLUGINFILES) or []
        saved, errors = [], []
        for f in files[:40]:
            try:
                path = self._click_download(f['href'])
                if path:
                    saved.append(self._copy_into(path, outdir, self._suggested_name(f['href'])))
            except Exception as e:
                errors.append('%s: %r' % (f.get('text', '')[:40], e))
        return {'url': self.m.current_url(), 'files': files, 'saved': saved,
                'download_errors': errors}

    # ---------- course context (the big harvester) ----------
    def build_context(self, course_id, outdir=None, include_quizzes=True):
        course_id = str(course_id)
        info = self.open_course(course_id)
        time.sleep(1.0)
        mods = self.map_modules()
        if outdir is None:
            fname = re.sub(r'[\\/:*?"<>|]', '_', (info.get('title') or course_id))[:60]
            outdir = os.path.join(CTX_ROOT, 'course_%s' % course_id)
        os.makedirs(outdir, exist_ok=True)
        index = ['# Course context: %s' % info.get('title', ''),
                 'url: %s' % info.get('url', ''), '']
        results = []
        for mod in mods or []:
            mtype, mid, mname = mod.get('type'), mod.get('id'), mod.get('name', '')
            entry = {'type': mtype, 'id': mid, 'name': mname, 'section': mod.get('section', '')}
            try:
                if mtype == 'resource':
                    r = self.download_resource(mid, outdir)
                    entry['saved'] = r.get('saved', [])
                elif mtype in ('page', 'label'):
                    self.m.navigate(mod['href'])
                    time.sleep(1.0)
                    entry['text'] = self.read_module_text(12000)
                elif mtype == 'assign':
                    r = self.download_assign(mid, outdir)
                    entry['text'] = r.get('text', '')[:12000]
                    entry['saved'] = r.get('saved', [])
                    entry['files'] = [f.get('text') for f in r.get('files', [])]
                elif mtype == 'folder':
                    r = self.download_folder(mid, outdir)
                    entry['saved'] = r.get('saved', [])
                elif mtype == 'url':
                    entry['link'] = True
                elif mtype == 'quiz' and include_quizzes:
                    entry['quiz'] = True  # details via quiz_status separately
                else:
                    entry['skipped'] = True
            except Exception as e:
                entry['error'] = repr(e)[:200]
            results.append(entry)
            index.append('## [%s] %s (id=%s) — section: %s' % (mtype, mname, mid, mod.get('section', '')))
            if entry.get('text'):
                safe = re.sub(r'[\\/:*?"<>|]', '_', mname)[:50] or mid
                with open(os.path.join(outdir, '%s_%s.txt' % (mtype, safe)), 'w', encoding='utf-8') as f:
                    f.write(entry['text'])
                index.append('   text -> %s_%s.txt' % (mtype, safe))
            for s in entry.get('saved', []):
                index.append('   file -> %s' % os.path.basename(s))
            if entry.get('link') or entry.get('quiz'):
                index.append('   url: %s' % mod.get('href', ''))
        with open(os.path.join(outdir, 'index.md'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(index))
        return {'course': info, 'outdir': outdir, 'modules': results,
                'index': index, 'module_count': len(mods or [])}

    # ---------- quiz engine ----------
    def quiz_status(self, quiz_id):
        self.m.navigate(ROOT + '/mod/quiz/view.php?id=%s' % quiz_id)
        time.sleep(1.0)
        return self.m.js(JS_QUIZ_STATE)

    def start_attempt(self, quiz_id, wait_url='attempt.php', timeout=25):
        self.m.navigate(ROOT + '/mod/quiz/view.php?id=%s' % quiz_id)
        time.sleep(1.0)
        r = self.m.js(JS_START_ATTEMPT)  # step 1 (opens modal or direct)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1.5)
            try:
                u = self.m.current_url()
            except Exception:
                u = ''
            if wait_url in u:
                return {'url': u, 'attempt_id': (re.search(r'attempt=(\d+)', u) or [None, None])[1], 'step': r}
            # try pressing the modal button too
            r2 = self.m.js(JS_START_ATTEMPT)
            if r2 == 'none':
                pass
        return {'url': self.m.current_url(), 'attempt_id': None, 'step': r, 'note': 'attempt page not detected'}

    def collect(self):
        return self.m.js(JS_COLLECT)

    def fill(self, answers):
        """answers: {'select': {field: option_text}, 'text': {field: value}}"""
        return self.m.js(_fill_js(answers))

    def _wait_for(self, substring, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1.2)
            try:
                u = self.m.current_url()
            except Exception:
                continue
            if substring in u:
                return u
        return self.m.current_url()

    def submit_attempt(self, verify_before=True, retries=4):
        """Finish -> submit, with 502 recovery. Returns result-style dict."""
        log('[submit] start at %s' % self.m.current_url())
        for attempt_no in range(retries):
            u = self.m.current_url()
            if u.startswith('data:') or '502' in (self.m.js('return document.title') or ''):
                u = ''
            # If not on quiz pages, recover
            if '/mod/quiz/' not in u:
                log('[submit] off-quiz url %s, recovering' % u)
            if 'review.php' in u:
                break
            if 'attempt.php' not in u and 'summary.php' not in u:
                # maybe quiz view with unfinished attempt -> continue
                self.m.js(JS_CONTINUE)
                time.sleep(2.0)
            if 'attempt.php' in self.m.current_url():
                self.m.js(JS_FINISH)
                self._wait_for('summary.php', 15)
            if 'summary.php' in self.m.current_url():
                self.m.js(JS_FORCE_SUBMIT)
                time.sleep(3.0)
                # detect 502 / processattempt stuck
                ttl = self.m.js('return document.title') or ''
                if '502' in ttl:
                    log('[submit] 502 hit, retry %d' % attempt_no)
                    time.sleep(8.0)
                    self.m.navigate(ROOT + '/my/')
                    time.sleep(2.0)
                    continue
                self._wait_for('review.php', 20)
            if 'review.php' in self.m.current_url():
                break
        return self.review()

    def review(self):
        return self.m.js(JS_REVIEW)

    def run_result(self):
        """Read attempt result page (grade) — safe after submit."""
        return self.m.js(JS_QUIZ_STATE)

    # ---------- web search in a SEPARATE tab ----------
    def _state_load(self):
        try:
            with open(STATE_FILE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _state_save(self, st):
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(st, f)

    def _ensure_search_tab(self):
        st = self._state_load()
        handles = self.m.handles()
        h = st.get('search_handle')
        if h and h in handles:
            self.m.switch(h)
            return h
        # open via javascript window.open, remember the new handle
        cur = handles[0] if handles else None
        self.m.js("window.open('about:blank','_blank'); return 'opened';")
        time.sleep(1.5)
        handles2 = self.m.handles()
        h = [x for x in handles2 if x not in handles]
        h = h[0] if h else (handles2[-1] if handles2 else cur)
        self.m.switch(h)
        st['search_handle'] = h
        # keep the quiz tab handle too
        if cur:
            st['main_handle'] = cur
        self._state_save(st)
        return h

    def back_to_main(self):
        st = self._state_load()
        h = st.get('main_handle')
        handles = self.m.handles()
        if h and h in handles:
            self.m.switch(h)
            return True
        return False

    def web_search(self, query, engine='ddg'):
        h = self._ensure_search_tab()
        url = 'https://lite.duckduckgo.com/lite/?q=' + urllib.parse.quote_plus(query)
        self.m.navigate(url)
        time.sleep(2.0)
        items = self.m.js(JS_SEARCH_RESULTS)
        return {'tab': h, 'engine_url': url, 'results': items}

    def read_url(self, url, max_chars=16000, stay_in_search_tab=True):
        self._ensure_search_tab()
        self.m.navigate(url)
        time.sleep(1.8)
        t = self.m.js(JS_MAINTEXT)
        return {'url': self.m.current_url(), 'text': (t or '')[:max_chars]}

    def close_search_tab(self):
        st = self._state_load()
        h = st.get('search_handle')
        if not h:
            return False
        try:
            self.m.switch(h)
            res = self.m.close_window()
        except Exception:
            res = None
        st['search_handle'] = None
        self._state_save(st)
        handles = self.m.handles()
        main = st.get('main_handle')
        if main and main in handles:
            self.m.switch(main)
        elif handles:
            self.m.switch(handles[0])
        return True


def out(data):
    """Log structured result + flush report."""
    log(json.dumps(data, ensure_ascii=False, indent=1))
    flush()
    return data