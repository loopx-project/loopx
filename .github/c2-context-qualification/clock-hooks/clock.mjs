const OriginalDate=Date;
const epoch=Date.parse("2026-10-29T01:00:00Z");
class FixedDate extends OriginalDate {
  constructor(...args) { super(...(args.length ? args : [epoch])); }
  static now() { return epoch; }
}
globalThis.Date=FixedDate;
