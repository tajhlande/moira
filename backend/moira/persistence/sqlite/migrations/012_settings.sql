CREATE TABLE IF NOT EXISTS settings (
    scope    TEXT NOT NULL
             CHECK(scope IN ('system', 'user', 'project', 'conversation')),
    scope_id TEXT NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT NOT NULL,
    PRIMARY KEY (scope, scope_id, key)
);
