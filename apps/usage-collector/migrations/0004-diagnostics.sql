-- Additive: retain v0/v1 history and rollback tables, never relabel old counts.
CREATE TABLE IF NOT EXISTS diagnostic_counts (
  receipt_day TEXT NOT NULL, activity_day TEXT NOT NULL, version TEXT NOT NULL,
  context TEXT NOT NULL, feature TEXT NOT NULL, operation TEXT NOT NULL,
  outcome TEXT NOT NULL, error TEXT NOT NULL, duration TEXT NOT NULL,
  signal TEXT NOT NULL, count INTEGER NOT NULL,
  PRIMARY KEY (receipt_day, activity_day, version, context, feature, operation, outcome, error, duration, signal)
);
