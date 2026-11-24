INSERT INTO checkpoints (job, key, status, cursor, attempts, error)
VALUES ($1, $2, $3, $4, CASE WHEN $3 = 'failed' THEN 1 ELSE 0 END, $5)
ON CONFLICT (job, key) DO UPDATE SET
    status = excluded.status,
    cursor = excluded.cursor,
    attempts = CASE WHEN excluded.status = 'failed' THEN checkpoints.attempts + 1 ELSE checkpoints.attempts END,
    error = excluded.error,
    updated_at = now()
