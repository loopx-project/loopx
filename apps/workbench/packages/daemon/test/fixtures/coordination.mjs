import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';

// Deliberately use the same CLI a real agent would execute, with its injected environment.
export function coordinationFixture(text) {
  const body = text.split(/<calli-(?:coordination(?:-note)?|team)>/)[0].trim();
  if (text.includes('<calli-team>')) {
    if (body.endsWith('TEAM_ENV')) return `calli-ready:${Boolean(process.env.CALLI_TEAM_SOCKET && process.env.CALLI_TEAM_KEY)}`;
    if (body.endsWith('TEAM_FINISH')) return 'Task finished after continuation.';
    if (body.endsWith('TEAM_STOP')) return '@dev slow\n@reviewer TEAM_MULTI_REVIEW';
    if (body.endsWith('TEAM_SHORT')) return '@dev TEAM_SHORT_WORK';
    if (body.endsWith('TEAM_SHORT_WORK')) return '@lead TEAM_FINISH';
    if (body.endsWith('TEAM_MEMBER_ERROR')) return '@dev fail';
    if (body.endsWith('TEAM_WAIT_FLOW')) return '@dev TEAM_WAIT_DRAFT';
    if (body.endsWith('TEAM_WAIT_DRAFT')) return '@reviewer TEAM_REVIEW_WAIT';
    if (body.endsWith('TEAM_CYCLE')) return '@dev TEAM_CYCLE_DEV';
    if (body.endsWith('TEAM_CYCLE_DEV')) return '@reviewer TEAM_CYCLE_REVIEW';
    if (body.endsWith('TEAM_CYCLE_REVIEW')) return '@lead TEAM_FINISH';
    if (body.endsWith('TEAM_MULTI')) return '@dev TEAM_MULTI_DEV\n@reviewer TEAM_MULTI_REVIEW';
    if (body.endsWith('TEAM_MULTI_DEV')) return '@lead Developer finished';
    if (body.endsWith('TEAM_MULTI_REVIEW')) return 'Reviewer finished';
    if (body.endsWith('Developer finished') || body.endsWith('Reviewer finished')) return 'All available results summarized.';
    if (body.endsWith('TEAM_CUSTOM')) return '@lead Do TEAM_CUSTOM_WORK';
    if (body.endsWith('TEAM_FLOW')) return '@dev Implement TEAM_DRAFT';
    if (body.endsWith('TEAM_REWORK')) return '@dev Implement TEAM_DRAFT_REWORK';
    if (body.endsWith('TEAM_DRAFT_REWORK')) return '@reviewer Review TEAM_REVIEW_ONE';
    if (body.endsWith('TEAM_REVIEW_ONE')) return '@dev Fix TEAM_FIX_ONE';
    if (body.endsWith('TEAM_FIX_ONE')) return '@reviewer Review TEAM_REVIEW_TWO';
    if (body.endsWith('TEAM_REVIEW_TWO')) return '@dev Fix TEAM_FIX_TWO';
    if (body.endsWith('TEAM_FIX_TWO')) return '@reviewer Review TEAM_REVIEW';
    if (body.endsWith('TEAM_DRAFT')) return '@reviewer Review TEAM_REVIEW';
    if (body.endsWith('TEAM_REVIEW')) return 'Reviewed TEAM_APPROVED';
    if (body.endsWith('TEAM_APPROVED')) return '@lead Completed TEAM_DELIVERED';
    if (body.endsWith('TEAM_DELIVERED')) return 'Implementation and review complete.';
    if (body.endsWith('TEAM_BUDGET')) return '@dev Loop TEAM_LOOP';
    if (body.endsWith('TEAM_LOOP')) return '@lead Continue TEAM_BUDGET';
    if (body.endsWith('TEAM_BUDGET')) return '@dev Loop TEAM_LOOP';
  }
  if (body.endsWith('CALLI_REPORT')) return '文档审查完成。\n\n已检查接口约定、目录分工和异常处理。建议补充重启后的恢复步骤，测试覆盖其余预期行为。';
  if (body === 'CALLI_ENV' || body.endsWith('\nCALLI_ENV')) return `calli-ready:${Boolean(process.env.CALLI_TEAM_SOCKET && process.env.CALLI_TEAM_KEY)};cwd:${process.cwd()}`;
  if (body === 'CALLI_HANDOVER_ENV') {
    const ready = Boolean(process.env.CALLI_HANDOVER_SOCKET && process.env.CALLI_HANDOVER_KEY);
    return `handover-ready:${ready};collaboration:${Boolean(process.env.CALLI_TEAM_SOCKET && process.env.CALLI_TEAM_KEY)};${ready ? execFileSync('calli', ['handover'], { encoding: 'utf8' }).trim() : ''}`;
  }
  const command = /CALLI_COMMAND:([A-Za-z0-9+/=]+)/.exec(body);
  if (command && !body.startsWith('[协作消息')) {
    const { args, text: message = '' } = JSON.parse(Buffer.from(command[1], 'base64').toString());
    return execFileSync('calli', args, { input: message, encoding: 'utf8' }).trim();
  }
  if (body.endsWith('CALLI_WRITE')) {
    writeFileSync('review-document.md', '# Design\n\nFirst draft.\n');
    execFileSync('calli', ['send', 'reviewer', '--message-file', '-', '--request-id', 'review-draft'], { input: 'CALLI_REVIEW', encoding: 'utf8' });
    return '文档初稿已写入 review-document.md，已请求审查。';
  }
  if (body.endsWith('CALLI_REVIEW')) return `审查完成：${readFileSync('review-document.md', 'utf8').includes('First draft') ? '请补充异常处理。' : '未找到初稿。'}`;
  if (body.includes('请补充异常处理。') && body.startsWith('[协作消息')) {
    writeFileSync('review-document.md', '# Design\n\nFirst draft.\n\n## Errors\nReport failures.\n');
    return '已根据审查补充异常处理。';
  }
  if (body.startsWith('[协作消息') && body.includes('状态：')) return '已收到成员结果，将继续检查并汇总。';
  if (body.startsWith('请审查接口文档')) return '已收到协作要求。';
  // A remote member that leaves the report the handoff asked for, in the file it named.
  if (body.includes('CALLI_RESULT_FILE')) {
    const target = /把完整结果写到[：:\s]+(\S+?)(?:；|\s|$)/.exec(body)?.[1];
    if (target) writeFileSync(target, '# REPORT\n\nfrom the fixture\n');
    return '结果已写入文件。';
  }
  return null;
}
