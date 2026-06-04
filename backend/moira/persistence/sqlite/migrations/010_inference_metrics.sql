CREATE TABLE inference_metrics (
    model TEXT NOT NULL,
    purpose TEXT NOT NULL,
    period_hour TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    thinking_tokens INTEGER NOT NULL DEFAULT 0,
    prompt_time_ms REAL NOT NULL DEFAULT 0,
    gen_time_ms REAL NOT NULL DEFAULT 0,
    UNIQUE(model, purpose, period_hour)
);

CREATE INDEX idx_inference_metrics_period ON inference_metrics(period_hour);
CREATE INDEX idx_inference_metrics_model ON inference_metrics(model);
CREATE INDEX idx_inference_metrics_purpose ON inference_metrics(purpose);
