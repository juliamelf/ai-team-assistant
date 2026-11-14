База PostgreSQL 16 ИИ-ассистента.

- `migrations/` — скрипты установки по порядку: `01_schema` → `02_seed` → `03_functions` → `04_v5_bot` → `05_speech_date` → `06_audio_pseudo` → `07_schema_1_0`. Все идемпотентные. `07` запускается последним: повторный запуск `02` и `03` возвращает старые объекты, `07` снова приводит их к схеме выжимки 1.0. `02_seed` с данными команды здесь не хранится.
- `build/` — `07_schema_1_0.sql` собирается из шаблона и словаря тем: `python3 db/build/build_007.py db/seed/matrix/topics.csv db/build/07_schema_1_0.template.sql db/migrations/07_schema_1_0.sql`. Правится шаблон, а не готовый файл.
- `seed/matrix/` — матрица компетенций QA, разобранная в CSV (`src/matrix/parse_matrix.py`), и словарь тем `topics.csv` (`src/matrix/build_topics.py`).
- `tests/` — `bash db/tests/run_all.sh`: пересоздаёт базу `assistant_test` на локальном PostgreSQL, применяет миграции с тестовым `02_seed_test.sql` и сверяет `save_summary` с JSON-схемой. Не запускать на рабочей базе.
