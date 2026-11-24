CREATE TABLE checkpoints (
    job text,
    key text,
    status text DEFAULT 'pending' NOT NULL,
    cursor jsonb DEFAULT '{}' NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    error text,
    updated_at timestamptz DEFAULT now() NOT NULL,
    CONSTRAINT checkpoints_pkey PRIMARY KEY (job, key)
);

CREATE INDEX checkpoints_status ON checkpoints (job, status);
