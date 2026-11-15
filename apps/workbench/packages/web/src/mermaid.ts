/**
 * Mermaid diagrams in answers: an agent describing an architecture in a ```mermaid block gets a
 * drawing instead of syntax. The library is a megabyte, so it is loaded the first time a diagram
 * actually appears — a conversation without one never pays for it — and it re-renders with the
 * theme when the look changes. Anything it cannot parse stays code, the way a broken SVG does.
 */

let loading: Promise<typeof import('mermaid').default> | null = null;
// initialize changes global configuration; keep it paired with its render.
let rendering: Promise<unknown> = Promise.resolve();

/**
 * The theme's own colours, read from the page's variables so a diagram follows the look — light,
 * dark, whatever palette — instead of mermaid's purple defaults. Read at render time, since the
 * look can change under a drawing that is already on screen.
 */
function themeVariables(): Record<string, string> {
  const styles = getComputedStyle(document.documentElement);
  const v = (name: string): string => styles.getPropertyValue(name).trim();
  return {
    background: v('--bg'),
    fontFamily: v('--font'),
    fontSize: '14px',
    // Nodes: the page's quiet surfaces, its borders, its text. Nothing purple, nothing loud.
    primaryColor: v('--surface-2'),
    primaryBorderColor: v('--border-strong'),
    primaryTextColor: v('--text'),
    secondaryColor: v('--surface'),
    secondaryBorderColor: v('--border-strong'),
    secondaryTextColor: v('--text'),
    tertiaryColor: v('--surface-2'),
    tertiaryBorderColor: v('--border'),
    tertiaryTextColor: v('--text'),
    mainBkg: v('--surface-2'),
    nodeBorder: v('--border-strong'),
    nodeTextColor: v('--text'),
    classText: v('--text'),
    textColor: v('--text'),
    titleColor: v('--text'),
    lineColor: v('--muted'),
    arrowheadColor: v('--muted'),
    edgeLabelBackground: v('--bg'),
    clusterBkg: v('--surface-2'),
    clusterBorder: v('--border-strong'),
    // Sequence diagrams: the same few colours in the roles they ask for.
    actorBkg: v('--surface-2'),
    actorBorder: v('--border-strong'),
    actorTextColor: v('--text'),
    actorLineColor: v('--border-strong'),
    signalColor: v('--muted'),
    signalTextColor: v('--text'),
    labelBoxBkgColor: v('--surface'),
    labelBoxBorderColor: v('--border-strong'),
    labelTextColor: v('--text'),
    loopTextColor: v('--text'),
    noteBkgColor: v('--surface-2'),
    noteBorderColor: v('--border-strong'),
    noteTextColor: v('--text'),
    activationBkgColor: v('--surface'),
    activationBorderColor: v('--border-strong'),
    sequenceNumberColor: v('--text'),
    // Class diagrams' striped rows, and state diagrams' second band.
    attributeBackgroundColorOdd: v('--surface-2'),
    attributeBackgroundColorEven: v('--surface'),
    altBackground: v('--surface-2'),
    // Where mermaid wants emphasis, it gets the app's accent, not a hue of its own.
    accentColor: v('--accent'),
  };
}

/** Renders one diagram to the SVG markup mermaid produces, themed as the page is. */
export async function renderMermaidDiagram(code: string, dark = document.documentElement.dataset.theme === 'dark'): Promise<string> {
  loading ??= import('mermaid').then((m) => m.default);
  const variables = themeVariables();
  const result = rendering.then(async () => {
    const mermaid = await loading!;
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      darkMode: dark,
      theme: 'base',
      themeVariables: variables,
    });
    const { svg } = await mermaid.render(`mmd-${Math.random().toString(36).slice(2)}`, code);
    return svg;
  });
  rendering = result.catch(() => undefined);
  return result;
}

/** Isolate the enlarged inline SVG's IDs, including its CSS and marker references. Called on open. */
export function enlargeMermaidDiagram(svg: string): string {
  // Mermaid's HTML labels can contain <br> and named entities. Parse like the inline preview.
  const document = new DOMParser().parseFromString(svg, 'text/html');
  const ids = [...document.querySelectorAll('[id]')].map(node => node.id);
  const prefix = `enlarged-${Math.random().toString(36).slice(2)}-`;
  for (const node of document.querySelectorAll('[id]')) node.id = prefix + node.id;
  for (const node of document.querySelectorAll('[aria-labelledby], [aria-describedby]')) {
    for (const attribute of ['aria-labelledby', 'aria-describedby']) {
      const value = node.getAttribute(attribute);
      if (value) node.setAttribute(attribute, value.split(/\s+/).map(id => ids.includes(id) ? prefix + id : id).join(' '));
    }
  }
  let markup = new XMLSerializer().serializeToString(document.querySelector('svg')!);
  // Match longer IDs first: a diagram's root ID can prefix its marker IDs.
  for (const id of ids.sort((a, b) => b.length - a.length)) {
    const escaped = id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    markup = markup.replace(new RegExp(`#${escaped}(?=[\\s).,;:"'\\]{}>]|$)`, 'g'), `#${prefix}${id}`);
  }
  return markup;
}
