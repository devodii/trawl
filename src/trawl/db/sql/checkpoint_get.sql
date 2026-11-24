SELECT
    status,
    cursor,
    attempts
FROM checkpoints
WHERE job = $1 AND key = $2
