from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Observation, PathMetric

SCHEMA_VERSION = 1


class StorageError(Exception):
    pass


def database_path(root: Path) -> Path:
    return root / ".driftlock" / "history.sqlite3"


def connect(root: Path) -> sqlite3.Connection:
    path = database_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY, check_id INTEGER NOT NULL,
                observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status INTEGER NOT NULL, content_type TEXT NOT NULL,
                latency_ms REAL NOT NULL, response_bytes INTEGER NOT NULL,
                is_json INTEGER NOT NULL,
                FOREIGN KEY(check_id) REFERENCES checks(id)
            );
            CREATE TABLE IF NOT EXISTS path_observations (
                observation_id INTEGER NOT NULL, path TEXT NOT NULL,
                types TEXT NOT NULL, string_values TEXT NOT NULL,
                array_length INTEGER, occurrences INTEGER NOT NULL,
                PRIMARY KEY(observation_id, path),
                FOREIGN KEY(observation_id) REFERENCES observations(id)
            );
            """
        )
        row = connection.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            connection.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        elif row["version"] != SCHEMA_VERSION:
            raise StorageError(
                f"Unsupported history database version {row['version']}; expected {SCHEMA_VERSION}"
            )
        connection.commit()
        return connection
    except sqlite3.Error as exc:
        raise StorageError(f"Could not initialize local history: {exc}") from exc


def save_observation(root: Path, check_name: str, observation: Observation) -> None:
    import json

    try:
        with connect(root) as db:
            db.execute("INSERT OR IGNORE INTO checks(name) VALUES (?)", (check_name,))
            check_id = db.execute("SELECT id FROM checks WHERE name = ?", (check_name,)).fetchone()[
                0
            ]
            cursor = db.execute(
                """INSERT INTO observations
                (check_id, status, content_type, latency_ms, response_bytes, is_json)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    check_id,
                    observation.status,
                    observation.content_type,
                    observation.latency_ms,
                    observation.response_bytes,
                    observation.is_json,
                ),
            )
            observation_id = cursor.lastrowid
            db.executemany(
                """INSERT INTO path_observations
                (observation_id, path, types, string_values, array_length, occurrences)
                VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        observation_id,
                        metric.path,
                        json.dumps(metric.types),
                        json.dumps(metric.values),
                        metric.array_length,
                        metric.occurrences,
                    )
                    for metric in observation.paths
                ],
            )
    except sqlite3.Error as exc:
        raise StorageError(f"Could not save observation: {exc}") from exc


def load_observations(root: Path, check_name: str) -> list[Observation]:
    import json

    with connect(root) as db:
        rows = db.execute(
            """SELECT o.* FROM observations o JOIN checks c ON c.id = o.check_id
            WHERE c.name = ? ORDER BY o.id""",
            (check_name,),
        ).fetchall()
        result: list[Observation] = []
        for row in rows:
            path_rows = db.execute(
                "SELECT * FROM path_observations WHERE observation_id = ? ORDER BY path",
                (row["id"],),
            ).fetchall()
            metrics = tuple(
                PathMetric(
                    path=item["path"],
                    types=tuple(json.loads(item["types"])),
                    values=tuple(json.loads(item["string_values"])),
                    array_length=item["array_length"],
                    occurrences=item["occurrences"],
                )
                for item in path_rows
            )
            result.append(
                Observation(
                    status=row["status"],
                    content_type=row["content_type"],
                    latency_ms=row["latency_ms"],
                    response_bytes=row["response_bytes"],
                    is_json=bool(row["is_json"]),
                    paths=metrics,
                )
            )
        return result
