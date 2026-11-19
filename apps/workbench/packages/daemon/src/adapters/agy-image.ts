import type { BorrowedTool } from './borrowed-tools.js';
import { HttpError } from '../errors.js';

/** Image generation is delegated to the existing CLI, using its own login and native tool. */
export const agyImageTool: BorrowedTool = {
  name: 'image.generate',
  description: '通过此实例的原生生图工具生成一张图片，返回可读取的图片资源。',
  keywords: ['生图', '图片', '配图', 'image', 'generate', 'illustration'],
  permissionMode: 'auto_edit',
  resultKind: 'image',
  inputSchema: {
    type: 'object', additionalProperties: false,
    properties: { prompt: { type: 'string', minLength: 1, maxLength: 8000, description: '图片内容、风格、比例等要求。' } },
    required: ['prompt'],
  },
  prepare(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new HttpError(400, 'input must be an object');
    const input = raw as Record<string, unknown>;
    if (Object.keys(input).some(k => k !== 'prompt')) throw new HttpError(400, 'image.generate only accepts prompt');
    if (typeof input.prompt !== 'string' || !input.prompt.trim() || input.prompt.length > 8000) {
      throw new HttpError(400, 'prompt must contain 1–8000 characters');
    }
    const prompt = input.prompt.trim();
    return {
      input: { prompt },
      text: `Generate exactly one image using your native generate_image tool for this request:\n\n${prompt}\n\n` +
        'Use your native image generator: generate_image or the built-in image-generator subagent. ' +
        'When using image-generator, pass the full request, wait for its completion message, and use the returned image path. ' +
        'Do not read subagent transcripts or log files, or inspect unrelated files. ' +
        'Do not substitute SVG, code, a downloaded image, or a placeholder. ' +
        'Keep the image in this task workspace or your conversation artifact directory. ' +
        'Return a Markdown image link with its absolute local file path. If image generation is unavailable or fails, report that failure.',
    };
  },
  collect(events, answer) {
    const calls = new Set(events.filter(e => {
      if (e.type !== 'tool.start') return false;
      if (e.data.tool === 'generate_image') return true;
      const input = e.data.input as Record<string, unknown> | undefined;
      return (e.data.tool === 'invoke_subagent' && input?.TypeName === 'image-generator') || e.data.subagentType === 'image-generator';
    }).map(e => e.data.id));
    const completed = events.filter(e => e.type === 'tool.end' && calls.has(e.data.id) && e.data.status === 'completed');
    if (!completed.length) throw new Error('未观察到原生 generate_image 或 image-generator 调用成功；不能将文字回复当成生图结果。');
    const texts = [answer, ...completed.map(e => typeof e.data.output === 'string' ? e.data.output : JSON.stringify(e.data.output))];
    const paths = new Set<string>();
    for (const text of texts) {
      // Prefer complete quoted/Markdown paths, including spaces. Never interpret URLs as local files.
      for (const m of text.matchAll(/(?:\]\(\s*<?|["`])((?:file:\/\/)?\/[^\n"`<>]*?\.(?:png|jpe?g|webp|gif))(?=>?\s*(?:"[^"\n]*"|'[^'\n]*')?\s*\)|["`])/gi)) {
        let path = m[1].replace(/^file:\/\//, '');
        try { path = decodeURIComponent(path); } catch { /* Literal percent signs are valid. */ }
        paths.add(path);
      }
      for (const m of text.matchAll(/(?:^|[\s(])((?:file:\/\/)?\/[^\s"'`<>()]+\.(?:png|jpe?g|webp|gif))(?=$|[\s),.;])/gi)) {
        paths.add(m[1].replace(/^file:\/\//, ''));
      }
    }
    return { images: [...paths].slice(0, 12) };
  },
};
