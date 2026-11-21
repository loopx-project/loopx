import React from 'react';
import { createRoot } from 'react-dom/client';
import { ThreadView } from '/src/components/ThreadView.tsx';
import '/src/styles.css';

const diagram = `flowchart LR
  subgraph clients[浏览器与交互层]
    phone[手机浏览器] --> ui[对话界面]
    desktop[桌面浏览器] --> ui
    ui --> composer[消息与附件]
    ui --> reader[文件只读预览]
  end
  subgraph hub[Hub 协调层]
    api[HTTP API] --> threads[会话管理]
    threads --> events[事件与历史记录]
    threads --> routing[能力路由]
    routing --> collaboration[成员协作]
  end
  subgraph adapters[Agent 适配层]
    rpc[进程与协议] --> codex[编码 Agent]
    rpc --> planner[规划 Agent]
    rpc --> reviewer[审查 Agent]
  end
  subgraph storage[本地数据层]
    state[会话索引] --> history[只读历史]
    history --> files[项目文件]
    files --> preview[SVG 与文档预览]
  end
  composer --> api
  reader --> api
  collaboration --> rpc
  events --> state
  codex --> files
  reviewer --> preview`;
const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 200"><rect width="800" height="200" fill="steelblue"/></svg>';
const items = [{ kind: 'assistant', key: 'architecture', turnId: 'one', done: true,
  text: `## 远程 Agent 架构\n\n在手机和桌面浏览器中操作同一条对话。各层通过明确的协议连接，文件保持只读。\n\n\`\`\`mermaid\n${diagram}\n\`\`\`${window.testImages ? `\n\n\`\`\`svg\n${svg}\n\`\`\`` : ''}` }];
const noop = async () => new Blob();
createRoot(document.getElementById('root')).render(React.createElement('main', { style: { maxWidth: 1000, margin: 'auto', padding: 20 } },
  React.createElement(ThreadView, { items, roots: [], loadImage: noop, loadFile: noop })));
