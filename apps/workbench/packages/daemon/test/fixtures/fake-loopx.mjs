#!/usr/bin/env node
const args = process.argv.slice(2);
if (args.includes('--version')) console.log('loopx ' + (process.env.FAKE_LOOPX_VERSION || '1.4.2'));
else {
  const goal = args[args.indexOf('--goal-id') + 1];
  if (goal === 'slow') await new Promise(r => setTimeout(r, 60000));
  if (goal === 'invalid') console.log('not JSON');
  else console.log(JSON.stringify({ ok: true, goal_filter: goal, attention_queue: { items: goal === 'missing' ? [] : [{ goal_id: goal, status: 'active_state_agent_todo', goal_channel_projection: { display_name: 'Review the integration' }, recommended_action: 'Verify the plugin', user_todos: { total_count: 0, items: [] }, agent_todos: { total_count: 2, items: [{ todo_id: 'a1', text: 'Validate the shared projection', status: 'open' }] } }] } }));
}
