-- LoopX usage collector (Cloudflare D1). One row per installation per UTC day.
CREATE TABLE IF NOT EXISTS installation_usage (
  activity_day TEXT NOT NULL, install_id TEXT NOT NULL,
  version TEXT NOT NULL, context TEXT NOT NULL, revision INTEGER NOT NULL,
  cli TEXT NOT NULL, runtime TEXT NOT NULL, truncated INTEGER NOT NULL,
  receipt_day TEXT NOT NULL,
  PRIMARY KEY (activity_day, install_id)
);
CREATE INDEX IF NOT EXISTS installation_usage_id ON installation_usage (install_id);

CREATE TABLE IF NOT EXISTS installs (
  install_id TEXT PRIMARY KEY,
  first_day TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pings (
  day TEXT NOT NULL,
  install_id TEXT NOT NULL,
  version TEXT NOT NULL,
  os TEXT NOT NULL,
  python TEXT NOT NULL,
  channel TEXT NOT NULL,
  arch TEXT NOT NULL DEFAULT 'other',
  PRIMARY KEY (day, install_id)
);

CREATE INDEX IF NOT EXISTS pings_install ON pings (install_id);
CREATE INDEX IF NOT EXISTS installs_first_day ON installs (first_day);

-- Counters intentionally have no installation id or join key.
CREATE TABLE IF NOT EXISTS usage_counts (
  day TEXT NOT NULL,
  feature TEXT NOT NULL,
  outcome TEXT NOT NULL,
  duration TEXT NOT NULL,
  error TEXT NOT NULL,
  count INTEGER NOT NULL,
  PRIMARY KEY (day, feature, outcome, duration, error)
);

CREATE TABLE IF NOT EXISTS goal_usage_counts (
  day TEXT NOT NULL, span TEXT NOT NULL, execution TEXT NOT NULL, count INTEGER NOT NULL,
  PRIMARY KEY (day, span, execution)
);

CREATE TABLE IF NOT EXISTS goal_duration_counts (
  day TEXT NOT NULL, measurement TEXT NOT NULL, host TEXT NOT NULL,
  span TEXT NOT NULL, duration TEXT NOT NULL, count INTEGER NOT NULL,
  PRIMARY KEY (day, measurement, host, span, duration)
);

CREATE TABLE IF NOT EXISTS diagnostic_counts (
  receipt_day TEXT NOT NULL, activity_day TEXT NOT NULL, version TEXT NOT NULL,
  context TEXT NOT NULL, feature TEXT NOT NULL, operation TEXT NOT NULL,
  outcome TEXT NOT NULL, error TEXT NOT NULL, duration TEXT NOT NULL,
  signal TEXT NOT NULL, count INTEGER NOT NULL,
  PRIMARY KEY (receipt_day, activity_day, version, context, feature, operation, outcome, error, duration, signal)
);
