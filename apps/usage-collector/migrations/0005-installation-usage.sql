-- Additive. No historical linkage, context or runtime backfill.
CREATE TABLE IF NOT EXISTS installation_usage (
  activity_day TEXT NOT NULL, install_id TEXT NOT NULL,
  version TEXT NOT NULL, context TEXT NOT NULL, revision INTEGER NOT NULL,
  cli TEXT NOT NULL, runtime TEXT NOT NULL, truncated INTEGER NOT NULL,
  receipt_day TEXT NOT NULL,
  PRIMARY KEY (activity_day, install_id)
);
CREATE INDEX IF NOT EXISTS installation_usage_id ON installation_usage (install_id);
