import type { BorrowedTool } from './borrowed-tools.js';
import { searchInput, searchSchema, unavailableComputerTool, webResult } from './borrowed-web.js';

export const agySearchTool: BorrowedTool = {
  name: 'web.search',
  description: '使用此实例的原生 Google 搜索，返回回答与来源链接。',
  keywords: ['搜索', '联网', 'search', 'web', 'research', 'Google'],
  permissionMode: 'auto_edit', resultKind: 'text', inputSchema: searchSchema,
  prepare: raw => searchInput(raw, 'your native search_web tool'),
  collect: (events, answer) => webResult(events, answer, event => event.data.tool === 'search_web'),
};

export const agyBrowserTool = unavailableComputerTool('browser.use',
  '当前接入的是 Antigravity headless CLI，尚未接入 IDE 的 Browser Subagent 运行环境；启动信息中的工具名不代表本会话可调用。');
