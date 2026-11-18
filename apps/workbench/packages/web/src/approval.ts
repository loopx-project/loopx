/** Keep executable text out of the heading without losing any of the requested operation. */
export function approvalContent(title: string, command: string | null, commandTitle: string, defaultTitle: string): { title: string; detail: string | null } {
  const heading = title.trim();
  const isCommand = command !== null && heading === command.trim();
  const oversized = heading.length > 160 || /[\r\n]/.test(heading);
  return {
    title: isCommand || oversized || !heading ? command !== null ? commandTitle : defaultTitle : title,
    detail: oversized && !isCommand ? [title, command].filter(value => value !== null).join('\n\n') : command,
  };
}
