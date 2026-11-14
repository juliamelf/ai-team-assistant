"""Собирает 07_schema_1_0.sql: подставляет словарь тем из topics.csv в шаблон миграции.

Запуск:  python3 build_007.py topics.csv шаблон.sql результат.sql
"""
import csv
import sys


def q(s):
    return "'" + s.replace("'", "''") + "'" if s else "NULL"


def arr(s):
    items = [x.strip() for x in s.split(";") if x.strip()]
    return "ARRAY[" + ", ".join(q(x) for x in items) + "]::text[]" if items else "'{}'::text[]"


def main(topics_csv, template, out):
    rows = list(csv.DictReader(open(topics_csv, encoding="utf-8")))
    values = []
    for i, r in enumerate(rows, 1):
        values.append("    ({}, {}, {}, {}, {}, {}, {}, {})".format(
            q(r["topic_id"]), i, q(r["name"]), arr(r["speech_examples"]),
            q(r["manual"]), q(r["auto"]), q(r["load"]), q(r["basis"])))
    sql = open(template, encoding="utf-8").read()
    assert sql.count("--@@TOPICS@@") == 1
    sql = sql.replace("--@@TOPICS@@", ",\n".join(values))
    open(out, "w", encoding="utf-8").write(sql)
    print("тем:", len(rows))


if __name__ == "__main__":
    main(*sys.argv[1:4])
