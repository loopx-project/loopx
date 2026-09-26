CREATE TABLE IF NOT EXISTS goal_duration_counts (
  day TEXT NOT NULL, measurement TEXT NOT NULL, host TEXT NOT NULL,
  span TEXT NOT NULL, duration TEXT NOT NULL, count INTEGER NOT NULL,
  PRIMARY KEY (day, measurement, host, span, duration)
);
INSERT INTO goal_duration_counts (day, measurement, host, span, duration, count)
SELECT day, 'host_call', 'unknown', span, execution, count FROM goal_usage_counts
WHERE true ON CONFLICT (day, measurement, host, span, duration) DO NOTHING;
