CREATE TABLE users (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE model_preferences (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    intelligence_endpoint TEXT NOT NULL DEFAULT '',
    intelligence_model TEXT NOT NULL DEFAULT '',
    task_endpoint TEXT NOT NULL DEFAULT '',
    task_model TEXT NOT NULL DEFAULT ''
);

INSERT INTO users (id) VALUES ('default');
