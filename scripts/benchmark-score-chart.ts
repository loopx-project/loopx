// Render released observations only. The caller owns units, clock alignment and run selection.
export type ChartSeries = {
  label: string;
  points: { hour: number; value: number }[];
  observedUntil?: number;
};

export const armColors = ['#3f5e80', '#7b4398', '#007b70', '#b55219'];
const dashPatterns = ['', '7 3', '', '3 3'];
export function escapeChartText(value: string) {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

export function scoreChart(series: ChartSeries[], options: {
  best: boolean; higherIsBetter: boolean; hours: number; unit: string; axisDivisor?: number;
  marker?: { from: number; to: number; label: string };
}) {
  const {best, higherIsBetter, hours, marker} = options;
  if (!Number.isFinite(hours) || hours <= 0) throw new Error('Expected a positive chart duration');
  const runs = series.map(run => {
    let incumbent: number | undefined;
    let previousHour = 0;
    const points = run.points.filter(({hour, value}) => {
      if (!Number.isFinite(value) || !Number.isFinite(hour) || hour < previousHour || hour > hours)
        throw new Error('Score samples must be finite, ordered and within the time axis');
      previousHour = hour;
      if (!best) return true;
      if (incumbent !== undefined && (higherIsBetter ? value <= incumbent : value >= incumbent)) return false;
      incumbent = value;
      return true;
    });
    return {...run, points};
  });
  let low = Infinity, high = -Infinity;
  for (const run of runs) for (const point of run.points) {
    low = Math.min(low, point.value);
    high = Math.max(high, point.value);
  }
  if (!Number.isFinite(low)) return '<p>No observed scores.</p>';
  const pad = (high - low) * .07 || Math.max(Math.abs(high) * .001, .01);
  low -= pad;
  high += pad;
  const width = 960, left = 105, right = 932, top = 37, bottom = 281;
  const x = (hour: number) => (left + (right-left) * hour/hours).toFixed(2);
  const y = (score: number) => (bottom - (bottom-top) * (score-low)/(high-low)).toFixed(2);
  const divisor = options.axisDivisor ?? 1;
  if (!Number.isFinite(divisor) || divisor <= 0) throw new Error('Expected a positive score axis divisor');
  const targetStep = (high-low)/4;
  const magnitude = 10 ** Math.floor(Math.log10(targetStep));
  const step = ([1, 2, 2.5, 5, 10].find(n => n*magnitude >= targetStep) ?? 10) * magnitude;
  const precision = Math.max(0, Math.min(12, 1 - Math.floor(Math.log10(step/divisor))));
  const fmt = new Intl.NumberFormat('en-US', {maximumFractionDigits: precision});
  const title = best ? 'Best observed score' : 'Individual score observations';
  const svg = [`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} 322" role="img" aria-label="${title}" font-family="system-ui,sans-serif" font-size="13" fill="#4c5c59"><title>${title}</title>`];
  svg.push(`<text x="${left}" y="22">${escapeChartText(options.unit)}</text>`);
  if (marker) {
    if (!(0 <= marker.from && marker.from <= marker.to && marker.to <= hours)) throw new Error('Marker outside chart');
    svg.push(`<rect x="${x(marker.from)}" y="${top}" width="${Number(x(marker.to))-Number(x(marker.from))}" height="${bottom-top}" fill="#edf0ef"/>`,
      `<path d="M${x(marker.to)} ${top}V${bottom}" stroke="#778780" stroke-dasharray="3 5"/><text x="${x(marker.to)}" y="22" text-anchor="middle">${escapeChartText(marker.label)}</text>`);
  }
  for (let tick = Math.ceil(low / step); tick <= Math.floor(high / step); tick++) {
    const score = tick * step;
    svg.push(`<path d="M${left} ${y(score)}H${right}" stroke="#e4e9e7"/><text x="${left-10}" y="${Number(y(score))+4}" text-anchor="end">${fmt.format(score / divisor)}</text>`);
  }
  for (let i = 0; i <= 6; i++) {
    const hour = hours*i/6;
    svg.push(`<text x="${x(hour)}" y="${bottom+26}" text-anchor="middle">${Number(hour.toPrecision(3))} h</text>`);
  }
  runs.forEach(({points, label, observedUntil}, index) => {
    const last = points.at(-1);
    if (!last) return;
    const color = armColors[index % armColors.length];
    if (best) {
      const path = [`M${x(points[0].hour)} ${y(points[0].value)}`];
      for (const p of points.slice(1)) path.push(`H${x(p.hour)}V${y(p.value)}`);
      const horizon = observedUntil ?? series[index].points.at(-1)!.hour;
      if (!Number.isFinite(horizon) || horizon < series[index].points.at(-1)!.hour || horizon > hours)
        throw new Error('Observed horizon outside series');
      path.push(`H${x(horizon)}`);
      svg.push(`<path d="${path.join(' ')}" fill="none" stroke="${color}" stroke-width="2.8" stroke-dasharray="${dashPatterns[index % dashPatterns.length]}"/>`);
    }
    for (const point of points) {
      svg.push(`<circle cx="${x(point.hour)}" cy="${y(point.value)}" r="4" fill="${color}" stroke="white" stroke-width="1"><title>${escapeChartText(label)} · ${point.hour.toFixed(2)} h · ${point.value.toLocaleString('en-US',{maximumSignificantDigits:15})}</title></circle>`);
    }
  });
  return svg.join('') + '</svg>';
}
