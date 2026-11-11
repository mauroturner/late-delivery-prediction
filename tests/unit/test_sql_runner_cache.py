"""The SQL runner caches what it computes from a file's text (U15 speed-up of the fast suite): a cached answer is never stale and never shared (RNF02, AC36)."""

from __future__ import annotations

import os
from pathlib import Path

from latedel.warehouse import _declared_tables, split_statements

SCRIPT = "CREATE TABLE a (x INTEGER);\nCREATE TRIGGER t BEFORE INSERT ON a BEGIN SELECT 1; SELECT 2; END;\nSELECT 3"


def test_the_statements_of_a_text_are_split_as_before_and_each_caller_gets_its_own_list():
    first = split_statements(SCRIPT)
    assert first == ["CREATE TABLE a (x INTEGER);", "CREATE TRIGGER t BEFORE INSERT ON a BEGIN SELECT 1; SELECT 2; END;", "SELECT 3"]
    first.append("DROP TABLE a")
    first[0] = "changed"
    assert split_statements(SCRIPT) == ["CREATE TABLE a (x INTEGER);", "CREATE TRIGGER t BEFORE INSERT ON a BEGIN SELECT 1; SELECT 2; END;", "SELECT 3"]
    assert split_statements("-- only a comment\n;") == [] and split_statements(SCRIPT.replace("SELECT 3", "SELECT 4"))[-1] == "SELECT 4"


def test_the_declared_tables_follow_the_file_when_it_changes(tmp_path: Path):
    schema = tmp_path / "schema.sql"
    schema.write_text("CREATE TABLE a (x INTEGER);", encoding="utf-8")
    assert _declared_tables(schema) == {"a": {"x": "INTEGER"}}
    assert _declared_tables(schema) == {"a": {"x": "INTEGER"}}, "the same file, the same answer"
    schema.write_text("CREATE TABLE a (x INTEGER, y TEXT);", encoding="utf-8")
    assert _declared_tables(schema) == {"a": {"x": "INTEGER", "y": "TEXT"}}, "a longer file is read again"
    same_size = "CREATE TABLE a (x INTEGER, z TEXT);"
    schema.write_text(same_size, encoding="utf-8")
    stat = schema.stat()
    os.utime(schema, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert _declared_tables(schema) == {"a": {"x": "INTEGER", "z": "TEXT"}}, "a file of the same size with another time is read again"
