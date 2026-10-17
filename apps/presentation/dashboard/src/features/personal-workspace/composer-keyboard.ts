/** Local keyboard intent; Session/Turn admission still owns every effect. */
export function composerKeyAction(event: {
  key: string;
  shiftKey: boolean;
  metaKey: boolean;
  repeat?: boolean;
  isComposing?: boolean;
  keyCode?: number;
}, composing: boolean): "send" | "steer" | undefined {
  // WebKit may end composition before the confirming keydown and report
  // isComposing=false. Its 229 key must still only commit the candidate.
  if (event.key !== "Enter" || event.shiftKey || event.repeat || composing
    || event.isComposing || event.keyCode === 229) return undefined;
  return event.metaKey ? "steer" : "send";
}
