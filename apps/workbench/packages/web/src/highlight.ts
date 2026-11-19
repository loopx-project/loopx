import hljs from 'highlight.js/lib/core';
import bash from 'highlight.js/lib/languages/bash';
import c from 'highlight.js/lib/languages/c';
import cmake from 'highlight.js/lib/languages/cmake';
import cpp from 'highlight.js/lib/languages/cpp';
import csharp from 'highlight.js/lib/languages/csharp';
import css from 'highlight.js/lib/languages/css';
import diff from 'highlight.js/lib/languages/diff';
import dockerfile from 'highlight.js/lib/languages/dockerfile';
import go from 'highlight.js/lib/languages/go';
import ini from 'highlight.js/lib/languages/ini';
import java from 'highlight.js/lib/languages/java';
import javascript from 'highlight.js/lib/languages/javascript';
import json from 'highlight.js/lib/languages/json';
import kotlin from 'highlight.js/lib/languages/kotlin';
import lua from 'highlight.js/lib/languages/lua';
import makefile from 'highlight.js/lib/languages/makefile';
import markdown from 'highlight.js/lib/languages/markdown';
import php from 'highlight.js/lib/languages/php';
import protobuf from 'highlight.js/lib/languages/protobuf';
import python from 'highlight.js/lib/languages/python';
import ruby from 'highlight.js/lib/languages/ruby';
import rust from 'highlight.js/lib/languages/rust';
import scss from 'highlight.js/lib/languages/scss';
import sql from 'highlight.js/lib/languages/sql';
import swift from 'highlight.js/lib/languages/swift';
import thrift from 'highlight.js/lib/languages/thrift';
import typescript from 'highlight.js/lib/languages/typescript';
import xml from 'highlight.js/lib/languages/xml';
import yaml from 'highlight.js/lib/languages/yaml';

/**
 * Syntax highlighting for the file viewer. This module is loaded only when a file is first shown
 * (see FilesPanel), so the highlighter and its languages stay out of the bundle the page starts with.
 */
const LANGUAGES = {
  bash,
  c,
  cmake,
  cpp,
  csharp,
  css,
  diff,
  dockerfile,
  go,
  ini,
  java,
  javascript,
  json,
  kotlin,
  lua,
  makefile,
  markdown,
  php,
  protobuf,
  python,
  ruby,
  rust,
  scss,
  sql,
  swift,
  thrift,
  typescript,
  xml,
  yaml,
};
for (const [name, language] of Object.entries(LANGUAGES)) hljs.registerLanguage(name, language);

/** Past this, a file is shown plain: highlighting would hold the page up for seconds. */
const MAX_HIGHLIGHTED = 300 * 1024;

/** The file as highlighted HTML, or null when it is to be shown as plain text. */
export function highlight(text: string, language: string): string | null {
  if (text.length > MAX_HIGHLIGHTED || !hljs.getLanguage(language)) return null;
  try {
    return hljs.highlight(text, { language, ignoreIllegals: true }).value;
  } catch {
    return null;
  }
}
