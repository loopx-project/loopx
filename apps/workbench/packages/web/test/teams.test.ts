import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Api, type TeamDefinition } from '../src/api.js';
import { renameTeamMember, removeTeamMember, teamRecipient } from '../src/teams.js';
const team: TeamDefinition = { name: 'Coding', entry: 'lead', members: [{ name: 'lead', agent: 'a' }, { name: 'dev', agent: 'b' }, { name: 'reviewer', agent: 'b' }], relations: [{ kind: 'delegate', from: ['lead'], to: 'dev' }, { kind: 'delegate', from: ['dev'], to: 'reviewer' }] };

test('team editor renames links and entry together and removes dangling relations', () => {
  const renamed = renameTeamMember(team, 0, 'coordinator');
  assert.equal(renamed.entry, 'coordinator'); assert.deepEqual(renamed.relations[0].from, ['coordinator']);
  assert.equal(team.entry, 'lead');
  assert.equal(renameTeamMember(team, 1, 'builder').relations[0].to, 'builder');
  const removed = removeTeamMember(team, 1);
  assert.equal(removed.members.length, 2); assert.deepEqual(removed.relations, []);
  assert.equal(removeTeamMember(team, 0).entry, 'dev');
});

test('team recipients and team API use the dedicated dispatch route with web source', async () => {
  assert.equal(teamRecipient('team:123'), '123'); assert.equal(teamRecipient('codex'), null);
  const requests: any[] = [];
  const api = new Api('test');
  (api as any).request = async (...args: any[]) => { requests.push(args); return {}; };
  await api.teams(); await api.team('1'); await api.teamTemplate(); await api.saveTeam(team); await api.saveTeam({ ...team, id: '1' });
  await api.importTeam(team); await api.exportTeam('1'); await api.deleteTeam('1');
  await api.dispatchTeam('1', 'Build', '/project');
  assert.deepEqual(requests.map(r => r.slice(0, 2)), [['GET', '/api/teams'], ['GET', '/api/teams/1'], ['GET', '/api/teams/template'], ['POST', '/api/teams'], ['PUT', '/api/teams/1'], ['POST', '/api/teams/import'], ['GET', '/api/teams/1/export'], ['DELETE', '/api/teams/1'], ['POST', '/api/teams/1/tasks']]);
  // The dispatch route always names its attachments, so a send without references says so explicitly.
  assert.deepEqual(requests.at(-1)[2], { text: 'Build', cwd: '/project', references: [], source: { kind: 'web' } });
  const images = [{ mimeType: 'image/png', data: 'aGVsbG8=' }];
  await api.dispatchTeam('1', '', '/project', images);
  assert.deepEqual(requests.at(-1), ['POST', '/api/teams/1/tasks', { text: '', images, references: [], cwd: '/project', source: { kind: 'web' } }]);
});
