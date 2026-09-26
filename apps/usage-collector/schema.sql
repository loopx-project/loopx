-- LoopX usage collector (Cloudflare D1). One row per installation per UTC day.
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
