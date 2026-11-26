/**
 * Mermaid diagrams in answers: an agent describing an architecture in a ```mermaid block gets a
 * drawing instead of syntax. The library is a megabyte, so it is loaded the first time a diagram
 * actually appears — a conversation without one never pays for it — and it re-renders with the
 * theme when the look changes. Anything it cannot parse stays code, the way a broken SVG does.
 */

let loading: Promise<typeof import('mermaid').default> | null = null;
// initialize changes global configuration; keep it paired with its render.
let rendering: Promise<unknown> = Promise.resolve();

/** The colour forms mermaid's parser already reads; anything else goes through the painter below. */
const PLAIN_COLOR = /^#[0-9a-f]{3,8}$|^rgba?\(/i;
const readColors = new Map<string, string>();
let painter: CanvasRenderingContext2D | null | undefined;

/**
 * A colour mermaid can parse. Every palette but paper builds its surfaces with color-mix(), and a
 * current browser hands that back as color(srgb …); mermaid knows only hex and rgb(), throws, and
 * the diagram quietly stays code. Painting the value and reading the pixel lets the browser do the
 * conversion, whatever syntax the palette used. Values are fully resolved already, so a cached
 * answer cannot outlive the look it was read under.
 */
function readableColor(value: string): string {
  if (!value || PLAIN_COLOR.test(value)) return value;
  const known = readColors.get(value);
  if (known !== undefined) return known;
  painter ??= document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  if (!painter) return value;
  painter.clearRect(0, 0, 1, 1);
  painter.fillStyle = value;
  painter.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = painter.getImageData(0, 0, 1, 1).data;
  const color = a === 255 ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
  readColors.set(value, color);
  return color;
}

/**
 * The theme's own colours, read from the page's variables so a diagram follows the look — light,
 * dark, whatever palette — instead of mermaid's purple defaults. Read at render time, since the
 * look can change under a drawing that is already on screen.
 */
function themeVariables(): Record<string, string> {
  const styles = getComputedStyle(document.documentElement);
  const v = (name: string): string => styles.getPropertyValue(name).trim();
  /** A colour variable, in a syntax mermaid reads. */
  const c = (name: string): string => readableColor(v(name));
  return {
    background: c('--bg'),
    fontFamily: v('--font'),
    fontSize: '14px',
    // Nodes: the page's quiet surfaces, its borders, its text. Nothing purple, nothing loud.
    primaryColor: c('--surface-2'),
    primaryBorderColor: c('--border-strong'),
    primaryTextColor: c('--text'),
    secondaryColor: c('--surface'),
    secondaryBorderColor: c('--border-strong'),
    secondaryTextColor: c('--text'),
    tertiaryColor: c('--surface-2'),
    tertiaryBorderColor: c('--border'),
    tertiaryTextColor: c('--text'),
    mainBkg: c('--surface-2'),
    nodeBorder: c('--border-strong'),
    nodeTextColor: c('--text'),
    classText: c('--text'),
    textColor: c('--text'),
    titleColor: c('--text'),
    lineColor: c('--muted'),
    arrowheadColor: c('--muted'),
    edgeLabelBackground: c('--bg'),
    clusterBkg: c('--surface-2'),
    clusterBorder: c('--border-strong'),
    // Sequence diagrams: the same few colours in the roles they ask for.
    actorBkg: c('--surface-2'),
    actorBorder: c('--border-strong'),
    actorTextColor: c('--text'),
    actorLineColor: c('--border-strong'),
    signalColor: c('--muted'),
    signalTextColor: c('--text'),
    labelBoxBkgColor: c('--surface'),
    labelBoxBorderColor: c('--border-strong'),
    labelTextColor: c('--text'),
    loopTextColor: c('--text'),
    noteBkgColor: c('--surface-2'),
    noteBorderColor: c('--border-strong'),
    noteTextColor: c('--text'),
    activationBkgColor: c('--surface'),
    activationBorderColor: c('--border-strong'),
    sequenceNumberColor: c('--text'),
    // Class diagrams' striped rows, and state diagrams' second band.
    attributeBackgroundColorOdd: c('--surface-2'),
    attributeBackgroundColorEven: c('--surface'),
    altBackground: c('--surface-2'),
    // Where mermaid wants emphasis, it gets the app's accent, not a hue of its own.
    accentColor: c('--accent'),
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
