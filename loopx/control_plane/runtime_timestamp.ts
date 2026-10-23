/** Calendar-checked ISO codec; Date.parse alone silently rolls invalid dates. */
export function parseIsoTimestamp(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|z|[+-]\d{2}(?::?\d{2})?)?)?$/u.exec(
    value.trim(),
  );
  if (match === null) return null;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, fraction, timezone] = match;
  const [year, month, day, hour, minute, second, millisecond] = [
    yearText,
    monthText,
    dayText,
    hourText ?? "0",
    minuteText ?? "0",
    secondText ?? "0",
    (fraction ?? "").slice(0, 3).padEnd(3, "0") || "0",
  ].map(Number);
  const endOfDay = hour === 24;
  if (
    endOfDay &&
    (minute !== 0 || second !== 0 || (fraction !== undefined && /[1-9]/u.test(fraction)))
  ) return null;
  const calendarHour = endOfDay ? 0 : hour;
  const calendar = new Date(0);
  calendar.setUTCHours(calendarHour, minute, second, millisecond);
  calendar.setUTCFullYear(year, month - 1, day);
  if (
    calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1 ||
    calendar.getUTCDate() !== day || calendar.getUTCHours() !== calendarHour ||
    calendar.getUTCMinutes() !== minute || calendar.getUTCSeconds() !== second ||
    calendar.getUTCMilliseconds() !== millisecond
  ) return null;
  if (hourText === undefined) return calendar;
  let text = value.trim().replace(" ", "T").replace(/z$/u, "Z");
  if (fraction !== undefined) text = text.replace(`.${fraction}`, `.${fraction.slice(0, 3)}`);
  if (timezone === undefined) text += "Z";
  else text = text.replace(/([+-]\d{2})$/u, "$1:00");
  const parsed = new Date(text);
  return Number.isNaN(parsed.valueOf()) ? null : parsed;
}

/** Compatibility codec for datetime.fromisoformat inputs used by Todo metadata.
 * It keeps microseconds and offset seconds, which a JS Date cannot represent.
 * Missing timezone means UTC by default. The explicit aware-Python mode is
 * for historical retry reads: datetime.fromisoformat(value.replace("Z", ...))
 * requires a zone, rejects lower-case z suffixes, but accepts z as a separator.
 * Existing Todo callers keep their original codec without this mode. */
export function parseTodoTimestampMicros(value: string, awarePythonDatetime = false): bigint | null {
  // The Todo wrapper replaces Z/z; the aware retry wrapper replaces only Z.
  const match = /^(\d{4}-\d{2}-\d{2}|\d{8}|\d{4}-W\d{2}(?:-[1-7])?|\d{4}W\d{2}[1-7]?)(?:([^Z])(.+))?$/u.exec(value);
  if (!match) return null;
  const [, date, separator, time] = match;
  if (separator === "z" && !awarePythonDatetime) return null;
  let calendar: Date | null;
  if (date.includes("W")) {
    const week = /^(\d{4})-?W(\d{2})(?:-?([1-7]))?$/.exec(date)!;
    const year = Number(week[1]), number = Number(week[2]), day = Number(week[3] ?? 1);
    if (year < 1 || number < 1 || number > 53) return null;
    calendar = parseIsoTimestamp(`${week[1]}-01-04`);
    if (!calendar) return null;
    calendar.setUTCDate(calendar.getUTCDate() - (calendar.getUTCDay() + 6) % 7 + (number - 1) * 7 + day - 1);
    const thursday = new Date(calendar);
    thursday.setUTCDate(thursday.getUTCDate() + 3 - (thursday.getUTCDay() + 6) % 7);
    if (thursday.getUTCFullYear() !== year || calendar.getUTCFullYear() > 9999) return null;
  } else {
    const expanded = date.includes("-") ? date : `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6)}`;
    calendar = parseIsoTimestamp(expanded);
    if (!calendar || calendar.getUTCFullYear() < 1) return null;
  }
  if (time === undefined) {
    if (awarePythonDatetime) return null;
    const result = BigInt(calendar.valueOf()) * 1000n;
    if (result < -62135596800000000n || result >= 253402300800000000n) return null;
    return result;
  }
  const parts = /^(.*?)(Z|z|[+-].*)?$/.exec(time)!;
  if (awarePythonDatetime && (!parts[2] || parts[2] === "z")) return null;
  function clock(raw: string, offset: boolean): bigint | null {
    const parsed = /^(\d{2})(?:(:?)(\d{2})(?:\2(\d{2}))?)?(?:[.,](\d+))?$/.exec(raw);
    if (!parsed) return null;
    const hour = Number(parsed[1]), minute = Number(parsed[3] ?? 0), second = Number(parsed[4] ?? 0);
    const fraction = parsed[5] ?? "";
    if (!offset && (
      hour > 23 || minute > 59 || second > 59
    )) return null;
    const seconds = hour * 3600 + minute * 60 + second;
    const micros = BigInt(seconds) * 1000000n + BigInt(fraction.padEnd(6, "0").slice(0, 6));
    if (offset && micros >= 86400000000n) return null;
    // Python treats an all-zero offset as UTC even with fractional seconds.
    return offset && seconds === 0 ? 0n : micros;
  }
  const local = clock(parts[1], false);
  if (local === null) return null;
  let offset = 0n;
  if (parts[2] && !["Z", "z"].includes(parts[2])) {
    const parsed = clock(parts[2].slice(1), true);
    if (parsed === null) return null;
    offset = parts[2][0] === "-" ? -parsed : parsed;
  }
  const result = BigInt(calendar.valueOf()) * 1000n + local - offset;
  // Todo normalization requires a representable UTC datetime. Historical
  // aware-datetime comparisons also accept valid local dates whose offset
  // places their instant outside that UTC range; no UTC datetime is built.
  // -62135596800000000n is 0001-01-01T00:00:00Z, 253402300800000000n is 10000-01-01T00:00:00Z.
  if (!awarePythonDatetime && (result < -62135596800000000n || result >= 253402300800000000n)) return null;
  return result;
}
