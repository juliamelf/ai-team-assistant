#!/bin/bash
# Пересоздаёт тестовую базу, собирает и применяет миграции 01–07 и запускает проверки.
# Запуск из корня репозитория:  bash db/tests/run_all.sh
# Нужен локальный PostgreSQL 16. Параметры подключения — PGHOST, PGPORT, PGUSER (по умолчанию localhost:5432, postgres).
# ВНИМАНИЕ: база assistant_test удаляется и создаётся заново — не запускать на рабочей базе.
set -e
cd "$(dirname "$0")/../.."
DB=assistant_test
P="psql -v ON_ERROR_STOP=1 -q"
python3 -I db/build/build_007.py db/seed/matrix/topics.csv db/build/07_schema_1_0.template.sql db/migrations/07_schema_1_0.sql
$P -d postgres -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" 2>&1 | grep -v NOTICE || true
for f in db/migrations/01_schema.sql db/tests/02_seed_test.sql db/migrations/03_functions.sql db/migrations/04_v5_bot.sql \
         db/migrations/05_speech_date.sql db/migrations/06_audio_pseudo.sql db/migrations/07_schema_1_0.sql; do
  $P -d $DB -f $f 2>&1 | grep -v NOTICE || true
done
PGDATABASE=$DB python3 db/tests/test_007.py schema/summary_schema_1.0.json
