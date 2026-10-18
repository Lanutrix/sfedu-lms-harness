# sfedu-lms-harness

English | **Русский**

**MCP-сервер + CLI для ИИ-агентов (Cursor, Claude Code, Codex, opencode, …), который проходит тесты на СЭО ИКТИБ ЮФУ** (`lms.sfedu.ru`) через твой настоящий браузер и твою же залогиненную сессию.

## Как это работает

1. Харнесс скачивает все материалы курса (PDF-словари, задания с файлами, страницы) в `context/`
2. Открывает тест и собирает все вопросы с вариантами (`tmp/questions.json`)
3. Агент отвечает по материалам; пробелы закрывает веб-поиском **в отдельной вкладке** — вкладка теста не покидает `lms.sfedu.ru`
4. Харнесс заполняет форму по имени поля, отправляет попытку (с авто-повторами на капризном сервере) и отдаёт оценку

## Быстрый старт

```bash
git clone https://github.com/Lanutrix/sfedu-lms-harness
cd sfedu-lms-harness

python lms.py courses            # твои курсы (браузер поднимется сам)
python lms.py context 4031       # скачать ВСЮ справку курса до тестов
python lms.py quiz 41146         # статус теста: попытки, оценки
python lms.py start 41146        # начать попытку
python lms.py collect            # все вопросы -> tmp/questions.json
python lms.py fill answers.json  # {"select": {поле: вариант}, "text": {поле: ответ}}
python lms.py submit             # отправить + прочитать оценку
```

Chrome вместо Firefox: префикс `--chrome` или `LMS_ENGINE=chrome` (профиль отдельный, куки LMS переносятся из Firefox автоматически).

## Установка в агентов

Сервер — обычный stdio MCP: `python <путь>/mcp_lms.py`.

| Агент | Куда |
|---|---|
| **Cursor** | `~/.cursor/mcp.json` (или `.cursor/mcp.json` в проекте) |
| **Claude Code** | `claude mcp add lms -- <путь>/mcp_lms.py` или файл `.mcp.json` |
| **Codex CLI** | `~/.codex/config.toml`: `[mcp_servers.lms]` |
| **opencode** | `opencode.json`: секция `"mcp"` |

```json
{
  "mcpServers": {
    "lms": {
      "command": "python",
      "args": ["<путь>/sfedu-lms-harness/mcp_lms.py"],
      "env": { "LMS_ENGINE": "firefox" }
    }
  }
}
```

MCP-инструменты повторяют команды: `lms_courses`, `lms_context`, `lms_quiz_status`, `lms_start_attempt`, `lms_collect_questions`, `lms_fill_answers`, `lms_submit_attempt`, `lms_get_review`, `lms_assign`, `lms_map`, `lms_web_search`, `lms_read_page`…

## Требования

Python 3.9+ (только stdlib, без `pip install`) и Firefox или Chrome/Edge — браузер харнесс запустит с нужным флагом сам; его отсутствие — понятная ошибка, а не молчаливый падёж.

## Заметки

- Ответы придумывает агент, харнесс лишь вносит переданное и отчитывается о каждом несопоставленном поле
- Материалы курсов — в `context/`, отчёты — в `tmp/`: в git они не попадают
- Ответственность за академическую этику использования — на пользователе

MIT — см. [LICENSE](LICENSE).