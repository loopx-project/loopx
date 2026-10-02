-- Owner-only D1 read queries. Bind :from_day and :through_day (UTC dates),
-- including the published CI-exclusion cutover and latest partial day.
-- Missing historical profiles are UNKNOWN, never zero runtime or no usage.
-- Do not publish installation IDs or cross-dimensional small cells.

-- Reporting state continuity, not people, work time or continuous uptime.
SELECT install_id, MIN(day) AS first_observed_day, MAX(day) AS last_observed_day,
       COUNT(*) AS observed_days,
       CAST(julianday(MAX(day)) - julianday(MIN(day)) AS INTEGER) + 1 AS calendar_span_days
FROM pings WHERE day BETWEEN :from_day AND :through_day
GROUP BY install_id;

-- Fixed CLI distribution. Exclusion covers explicitly declared days only.
SELECT json_extract(j.value, '$.feature') AS feature,
       COUNT(DISTINCT i.install_id) AS reporting_installations,
       SUM(json_extract(j.value, '$.count')) AS observed_calls
FROM installation_usage i, json_each(i.cli) j
WHERE i.activity_day BETWEEN :from_day AND :through_day AND i.context != 'maintainer'
GROUP BY feature;

-- Windowed installation runtime: deduplicated daily union, separate clocks.
-- Presence below one minute may report zero; absence must remain unknown.
SELECT i.install_id, json_extract(j.value, '$.measurement') AS measurement,
       COUNT(DISTINCT i.activity_day) AS measured_days,
       SUM(json_extract(j.value, '$.observed_minutes')) AS observed_minutes,
       MAX(i.truncated) AS contains_truncated_day
FROM installation_usage i, json_each(i.runtime) j
WHERE i.activity_day BETWEEN :from_day AND :through_day AND i.context != 'maintainer'
GROUP BY i.install_id, measurement;

-- Observe coverage before interpreting runtime/CLI as adoption.
SELECT context, COUNT(DISTINCT install_id) AS installations,
       COUNT(*) AS profile_days, SUM(truncated) AS truncated_days
FROM installation_usage WHERE activity_day BETWEEN :from_day AND :through_day
GROUP BY context;
