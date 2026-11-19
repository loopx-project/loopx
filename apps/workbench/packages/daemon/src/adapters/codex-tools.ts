import type { BorrowedTool } from './borrowed-tools.js';
import { readInput, readSchema, searchInput, searchSchema, webResult, webUrl } from './borrowed-web.js';

export const codexSearchTool: BorrowedTool = {
  name: 'web.search', description: '使用此实例的原生联网搜索，返回回答与来源链接。',
  keywords: ['搜索', '联网', 'search', 'web', 'research'],
  permissionMode: 'ask', resultKind: 'text', inputSchema: searchSchema,
  prepare: raw => searchInput(raw, 'your native web search tool'),
  collect: (events, answer) => webResult(events, answer, event =>
    event.data.tool === 'web_search' && (event.data.action as { type?: string })?.type === 'search'),
};

export const codexReadTool: BorrowedTool = {
  name: 'web.read', description: '使用此实例的原生网页工具读取指定 URL，概括正文或回答问题。',
  keywords: ['网页', '读取', '提取', 'read', 'fetch', 'url', 'web'],
  permissionMode: 'ask', resultKind: 'text', inputSchema: readSchema,
  prepare: raw => readInput(raw, 'the open action of your native web tool'),
  collect: (events, answer, input) => webResult(events, answer, event => {
    const action = event.data.action as { type?: string; url?: string } | undefined;
    const requested = typeof input.url === 'string' ? webUrl(input.url) : null;
    return event.data.tool === 'web_search' && action?.type === 'openPage' && !!requested
      && (webUrl(action.url ?? '') === requested || (Array.isArray(event.data.results)
        && event.data.results.some(result => typeof result?.url === 'string' && webUrl(result.url) === requested)));
  }),
};
