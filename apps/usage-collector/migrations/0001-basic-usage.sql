-- Existing v0 deployments only. Fresh deployments use schema.sql instead.
ALTER TABLE pings ADD COLUMN arch TEXT NOT NULL DEFAULT 'other';
CREATE TABLE usage_counts (
  day TEXT NOT NULL, feature TEXT NOT NULL, outcome TEXT NOT NULL,
  duration TEXT NOT NULL, error TEXT NOT NULL, count INTEGER NOT NULL,
  PRIMARY KEY (day, feature, outcome, duration, error)
);
