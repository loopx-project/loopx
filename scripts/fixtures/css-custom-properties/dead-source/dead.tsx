// Dead source and ordinary data cannot satisfy the paired CSS references.
export const live = 1;

// const sink = <div style={{ "--fixture-commented": "1px" }} />;

/* const other = <div style={{ "--fixture-block-commented": "1px" }} />; */

export const docs = 'a note about style: { "--fixture-stringified": "1px" }';

export const unboundConfig = { style: { "--fixture-unbound": "never applied" } };

// These examples describe supported sinks, but executing them only creates strings.
export const quotedJsx = '<div style={{ "--fixture-quoted-jsx": "red" }} />';
export const quotedCall = 'element.style.setProperty("--fixture-quoted-call", "red")';
export const quotedCast = '({ "--fixture-quoted-cast": "red" } as CSSProperties)';
export const quotedAnnotation = 'const s: CSSProperties = { "--fixture-quoted-annotation": "red" };';
export const templateExample = `<div style={{ "--fixture-template-jsx": "red" }} />`;
export const regexExample = /style={{ "--fixture-regex-jsx": "red" }}/;

const metadata = { setProperty(_key: string, _value: string) {} };
metadata.setProperty("--fixture-unrelated-call", "red");
const pickColor = (_data: unknown) => "red";
export const realSink = <div style={{
  "--fixture-real": "1px",
  color: pickColor({ "--fixture-nested-value": "red" }),
  content: '\'"--fixture-string-key": "red"\'',
}} />;
export const cssExample = '@property --fixture-css-text { syntax: "<color>"; }';
