# sfedu-lms-harness

**[English](README.md)** | Русский

**MCP-сервер + CLI-харнесс, который управляет твоим настоящим браузером на LMS (Moodle™): скачивает все материалы курса, читает и проходит тесты, ищет в интернете — всё через твою уже залогиненную сессию.**

Сделан для (и обкатан на) Moodle ЮФУ ИКТИБ («СЭО ИКТИБ ЮФУ», `lms.sfedu.ru`), но подход работает с любым Moodle — используются куки твоего реального браузера.

---

## Что умеет

| Возможность | Как |
|---|---|
| 📚 **Сбор материалов курса** | Обходит все модули курса: скачивает PDF-ресурсы, тексты заданий **и приложенные файлы**, страницы, содержимое папок → `context/<курс>/` + манифест `index.md` |
| 🧪 **Движок тестов** | Статус → начать попытку → собрать вопросы (тексты, опции, контексты) → заполнить ответы → отправить (с авто-восстановлением после **HTTP 502**) → прочитать оценки |
| 🌐 **Два движка браузера** | **Firefox** (протокол Marionette, порт 2828) или **Chrome/Edge** (CDP, порт 9222) — одинаковый API, одинаковые инструменты |
| 🔄 **Жизненный цикл браузера** | Сам запускает браузер с нужным отладочным флагом; перезапускает Firefox, если он был открыт без флага; вежливо отказывается, если браузер не установлен |
| 🔐 **Перенос сессии** | Для Chrome: переносит твои залогиненные куки LMS из живой сессии Firefox (через Marionette `WebDriver:GetCookies`) — ручной логин не нужен |
| 🔎 **Веб-поиск «непалившись»** | Поиск и чтение страниц в **отдельной вкладке**; вкладка с LMS никогда не покидает домен LMS |
| 🧠 **Агент — главный** | Генерация ответов остаётся за агентом (тобой); харнесс только рулит браузером и отчитывается про каждое заполненное поле |

Ноль Python-зависимостей — только stdlib (собственный клиент Marionette, собственный WebSocket-клиент для CDP).

## Структура репозитория

```
lmslib.py        ядро: движки (Marionette/CDP), операции LMS, движок тестов, скачивание
lms.py           CLI-харнесс
mcp_lms.py       MCP-сервер (stdio) — 14 инструментов
mcp.json.example пример конфигурации MCP
fx.py            автономный legacy-клиент Marionette (удобен для отладки живой сессии)
firefox_tabs.py  читает файлы сессии Firefox и показывает открытые вкладки (без Marionette)
```

Рабочие папки (создаются автоматически, в git не попадают):

```
context/        скачанные материалы курсов (PDF, DOCX, тексты + index.md)
tmp/            все одноразовые отчёты, дампы, скриншоты, стейты сессий
chrome_profile/  отдельный профиль автоматизации Chrome (хранит свой логин в LMS)
archive/        твои локальные одноразовые скрипты, если будут
```

## Требования

- **Python 3.9+** (только stdlib, ничего `pip install`-ить не надо)
- **Mozilla Firefox** — любая свежая сборка, *или*
- **Google Chrome / Microsoft Edge** (Chromium)
- Из коробки — Windows; macOS/Linux тоже работают (поправь пути браузеров в `lmslib.py`)

## Быстрый старт (CLI)

```bash
git clone https://github.com/Lanutrix/sfedu-lms-harness
cd sfedu-lms-harness

# список твоих курсов LMS (сам запустит/перезапустит браузер с нужным флагом)
python lms.py courses

# СНАЧАЛА скачать ВСЕ справочные материалы курса (до любых тестов)
python lms.py context 1294

# жизненный цикл теста
python lms.py quiz 42038          # статус: попытки, оценки (только чтение)
python lms.py start 42038         # начать попытку
python lms.py collect             # -> tmp/questions.json со всеми полями
#   ... агент/ты решаешь ответы по context/ ...
python lms.py fill answers.json   # {"select": {поле: текст_опции}, "text": {поле: значение}}
python lms.py submit              # завершить + отправить + лечение 502
python lms.py result              # оценки

# веб-поиск в отдельной вкладке (вкладка LMS остаётся на LMS)
python lms.py search "протокол marionette"
python lms.py read https://example.com
python lms.py close-search
```

Chrome вместо Firefox — префикс `--chrome` (или `set LMS_ENGINE=chrome`):

```bash
python lms.py --chrome courses
```

`python lms.py` без аргументов печатает полную справку по командам.

## MCP-инструменты

| Инструмент | Назначение |
|---|---|
| `lms_courses` | список курсов на дашборде |
| `lms_open_course` / `lms_map` | открыть курс / список его модулей |
| `lms_context` | скачать **все** материалы курса в `context/` |
| `lms_assign` | прочитать задание + скачать приложенные файлы |
| `lms_quiz_status` | остаток попыток, текущие оценки (только чтение) |
| `lms_start_attempt` | начать попытку теста |
| `lms_collect_questions` | полный дамп вопросов начатой попытки |
| `lms_fill_answers` | заполнить `{"select": {...}, "text": {...}}` (точное совпадение, потом подстрока) |
| `lms_submit_attempt` | завершить + отправить с авто-восстановлением 502 |
| `lms_get_review` | оценки по вопросам попытки |
| `lms_web_search` | веб-поиск в **отдельной вкладке** |
| `lms_read_page` | прочитать любой URL в этой же отдельной вкладке |
| `lms_close_search_tab` | закрыть поисковую вкладку, вернуться на LMS |

## Установка в агентов

Сервер — обычный **stdio MCP-сервер**: `python <путь>/mcp_lms.py`.

> Подставь свой абсолютный путь до репозитория
> (пример для Windows: `C:\\Users\\you\\tools\\sfedu-lms-harness\\mcp_lms.py`).

### Cursor

Область проекта: файл `.cursor/mcp.json` —
или для всех проектов: `%USERPROFILE%\.cursor\mcp.json` (macOS: `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "lms": {
      "command": "python",
      "args": ["C:\\Users\\you\\tools\\sfedu-lms-harness\\mcp_lms.py"],
      "env": { "LMS_ENGINE": "firefox" }
    }
  }
}
```

Перезапусти окно Cursor — инструменты `lms` появятся в списке.

### Claude Code

Одной командой:

```bash
claude mcp add lms -- /path/to/sfedu-lms-harness/mcp_lms.py
# или с движком chrome:
claude mcp add lms --env LMS_ENGINE=chrome -- /path/to/sfedu-lms-harness/mcp_lms.py
```

Или портативный файл `.mcp.json` в корне проекта:

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
# env = { LMS_ENGINE = "chrome" }   # опционально
```

### opencode

`opencode.json` (проект) или глобальный конфиг:

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

### Любой другой MCP-агент (Windsurf, Zed, Cline, Goose, …)

В настройках «custom MCP server / stdio» укажи:

```
command: python
args:    ["/path/to/sfedu-lms-harness/mcp_lms.py"]
env:     LMS_ENGINE=firefox   # или "chrome"
```

## Типовой сквозной процесс

```
1. lms_courses / lms_map          — найти курс и его тесты
2. lms_context <id курса>         — СНАЧАЛА собрать все справочные материалы
3. lms_quiz_status <id теста>     — проверить попытки/оценки, решить, действовать ли
4. lms_start_attempt              — открыть попытку
5. lms_collect_questions          — дамп всех полей (имена, опции, контексты)
   (агент отвечает, опираясь на context/; если чего-то не хватает →)
6. lms_web_search / lms_read_page — закрыть пробелы в отдельной вкладке
7. lms_fill_answers               — заполнить форму с проверкой каждого поля
8. lms_submit_attempt             — отправить (с авто-повторами при сбоях сервера)
9. lms_get_review                 — прочитать оценку; если попытки остались — повторить
```

## Переключатель `LMS_ENGINE`

- `firefox` (по умолчанию) — харнесс **перезапускает Firefox с `--marionette`**, если флаг отсутствует (вкладки восстановит сам Firefox).
- `chrome` — Chrome/Edge с `--remote-debugging-port` на **отдельном профиле** (`chrome_profile/`): твой повседневный браузер не трогается вообще. При первом использовании харнесс копирует куки залогиненной LMS из живого Firefox (fallback: `cookies.sqlite`, затем — дружелюбное «залогинься один раз в открывшемся окне»).

## Заметки и дисклеймеры

- Харнесс **никогда не выдумывает значения полей**: он вносит ровно то, что передал агент, и отчитывается о каждом поле, которое не смог сопоставить.
- Все отчёты, дампы и скриншоты пишутся в `tmp/`; скачанные материалы курсов — в `context/`. Ни то, ни другое в git не попадает.
- Материалы курсов в `context/` принадлежат их авторам (ЮФУ/составителям курса) — храни их локально.
- Ответственность за использование автоматизации на университетском LMS — на тебе. Этика и academic rules — уважай; см. раздел про веб-поиск: даже там вкладка теста строго остаётся на LMS.

## Лицензия

MIT — см. [LICENSE](LICENSE).