# -*- coding: utf-8 -*-
"""Read current Firefox tabs: window titles (active tab) + live session file."""
import ctypes, datetime, json, os
from ctypes import wintypes

_T = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tmp')
os.makedirs(_T, exist_ok=True)
REPORT = os.path.join(_T, 'firefox_tabs_report.txt')
lines = []

def out(s=''):
    lines.append(s)

# --- 1) Visible window titles (= active tab titles) via WinAPI ---
user32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
titles = []

@WNDENUMPROC
def enum_cb(hwnd, lparam):
    if user32.IsWindowVisible(hwnd):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            t = buf.value
            if 'Firefox' in t:
                titles.append((pid.value, t))
    return True

user32.EnumWindows(enum_cb, 0)
out('=== Visible Firefox window titles (active tab) ===')
for pid, t in titles:
    out(f'PID {pid}: {t}')
if not titles:
    out('(no visible Firefox windows found)')
out()

# --- 2) Live session: sessionstore-backups/recovery.jsonlz4 ---
def lz4_block_decode(data):
    i, n = 0, len(data)
    res = bytearray()
    while i < n:
        token = data[i]; i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = data[i]; i += 1; lit += b
                if b != 255:
                    break
        if lit:
            res += data[i:i + lit]
            i += lit
        if i >= n:
            break
        offset = data[i] | (data[i + 1] << 8); i += 2
        mat = token & 0xF
        if mat == 15:
            while True:
                b = data[i]; i += 1; mat += b
                if b != 255:
                    break
        mat += 4
        start = len(res) - offset
        if start < 0:
            raise ValueError('bad LZ4 offset')
        if mat > offset:
            for j in range(mat):
                res.append(res[start + j])
        else:
            res += res[start:start + mat]
    return bytes(res)

profiles_root = os.path.join(os.environ['APPDATA'], 'Mozilla', 'Firefox', 'Profiles')
out('=== Live session (sessionstore-backups/recovery.jsonlz4) ===')
found = False
for prof in sorted(os.listdir(profiles_root)):
    rec = os.path.join(profiles_root, prof, 'sessionstore-backups', 'recovery.jsonlz4')
    if not os.path.isfile(rec):
        continue
    raw = open(rec, 'rb').read()
    if raw[:8] != b'mozLz40\x00':
        continue
    try:
        sess = json.loads(lz4_block_decode(raw[12:]).decode('utf-8'))
    except Exception as e:
        out(f'[{prof}] ERROR: {e}')
        continue
    found = True
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(rec))
    out(f'--- Profile: {prof} | file updated: {mt} ---')
    for w in sess.get('windows', []):
        tabs = w.get('tabs', [])
        sel = w.get('selected', 1) or 1
        out(f'Window: {len(tabs)} tab(s), active tab # {sel}')
        for idx, tab in enumerate(tabs, 1):
            entries = tab.get('entries', [])
            eidx = tab.get('index', 1) or 1
            cur = entries[eidx - 1] if 0 < eidx <= len(entries) else {}
            mark = ' ACTIVE' if idx == sel else '       '
            out(f'{mark} [{idx}] {cur.get("title", "")}')
            out(f'          URL: {cur.get("url", "")}')
        out()
    out()
if not found:
    out('(no recovery files found)')

with open(REPORT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('WROTE', REPORT)