/** What a fenced block's language is called over it, and what the highlighter knows it as. */

/** Names agents give a shell block that the highlighter knows by another. */
const HIGHLIGHT_ALIASES: Record<string, string> = { shell: 'bash', console: 'bash', terminal: 'bash', yml: 'yaml', tsx: 'typescript', jsx: 'javascript', proto: 'protobuf' };
const LANGUAGE_NAMES: Record<string, string> = {
  bash: 'Bash', sh: 'Shell', shell: 'Shell', zsh: 'Zsh', console: 'Shell', terminal: 'Shell', powershell: 'PowerShell', ps1: 'PowerShell',
  js: 'JavaScript', javascript: 'JavaScript', jsx: 'JSX', ts: 'TypeScript', typescript: 'TypeScript', tsx: 'TSX',
  json: 'JSON', jsonc: 'JSON', yaml: 'YAML', yml: 'YAML', toml: 'TOML', ini: 'INI', xml: 'XML', html: 'HTML', svg: 'SVG',
  css: 'CSS', scss: 'SCSS', sql: 'SQL', py: 'Python', python: 'Python', go: 'Go', golang: 'Go', rs: 'Rust', rust: 'Rust',
  java: 'Java', kotlin: 'Kotlin', kt: 'Kotlin', swift: 'Swift', c: 'C', h: 'C', cpp: 'C++', 'c++': 'C++', cc: 'C++',
  cs: 'C#', csharp: 'C#', rb: 'Ruby', ruby: 'Ruby', php: 'PHP', lua: 'Lua', diff: 'Diff', patch: 'Diff',
  md: 'Markdown', markdown: 'Markdown', dockerfile: 'Dockerfile', makefile: 'Makefile', cmake: 'CMake',
  mermaid: 'Mermaid', thrift: 'Thrift', proto: 'Protobuf', protobuf: 'Protobuf', graphql: 'GraphQL',
};
/** How a block's language is named over it; plain text is named nothing. */
export function languageName(language: string): string | null {
  const key = language.toLowerCase();
  if (!key || ['text', 'txt', 'plain', 'plaintext', 'none', 'output'].includes(key)) return null;
  return LANGUAGE_NAMES[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
}

/** The highlighter's name for a block's language. */
export function highlightLanguage(language: string): string {
  const key = language.toLowerCase();
  return HIGHLIGHT_ALIASES[key] ?? key;
}
