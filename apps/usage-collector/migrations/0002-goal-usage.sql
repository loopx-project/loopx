CREATE TABLE IF NOT EXISTS goal_usage_counts (
  day TEXT NOT NULL, span TEXT NOT NULL, execution TEXT NOT NULL, count INTEGER NOT NULL,
  PRIMARY KEY (day, span, execution)
);
