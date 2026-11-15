import { createRoot } from 'react-dom/client';
import { useState } from 'react';
import { ExploreDialog } from '../src/components/ExploreDialog';
import type { Api, ExploreStatus } from '../src/api';

const snapshot = {
  personal: { agent: 'test', model: null, directory: '/example' },
  lark: { enabled: false, people: [], chats: [], intervalMin: 30, events: ['existing.event'] },
  brief: { enabled: false, morning: '09:00', evening: '19:00' },
  push: { enabled: false, recipient: '', baseUrl: '', waiting: true, failure: true, notice: true, scheduled: false },
  proactive: { mode: 'off', repos: [], enabled: false },
  profile: { text: 'Written by the person', isTemplate: false, rev: 4, path: 'profile.md', updatedAt: null },
};
const recommendation = {
  agent: { agent: 'test', model: 'small', options: [{ agent: 'test', model: 'small', label: 'Assistant', recommended: true, why: 'Available' }] },
  profile: { draft: 'A new draft', replace: false, why: 'From your conversations' },
  people: Array.from({length:8}, (_, i) => ({id:`person-${i}`, name:`Person ${i}`, selected:i === 0, role:i === 0 ? 'boss' : 'other', why:'Frequent contact', evidence:['Read-only evidence']})),
  chats: [{id:'group', name:'Project group', selected:true, why:'Active project', evidence:[]}],
  meetings: {on:true, why:'Recurring meeting', examples:['Weekly sync']}, intervalMin:{value:60,why:'Enough for your rhythm'},
  brief:{enabled:true,morning:'08:30',evening:'18:30',why:'Working hours'},
  push:{enabled:false,available:false,why:'Set a recipient first'}, proactive:{mode:'observe',why:'Start with observation'},
};
let run: ExploreStatus | null = null;
let reads = 0, failed = false;
const probe = { calls: [] as Array<{key:string;value?:unknown}>, reads:0, closed:false, applied:null as unknown };
Object.assign(window, { probe });
const save = async (key: keyof typeof snapshot, value: unknown) => {
  probe.calls.push({ key, value });
  if (key === 'brief' && !failed) { failed = true; throw new Error('Temporary save failure'); }
  return value;
};
const api = {
  exploreSources: async () => [{id:'calli-threads',available:true},{id:'lark-profile',available:false,reason:'Not logged in'}],
  startExplore: async () => { reads = 0; run = {id:'run',state:'collecting',startedAt:'now',steps:[]}; return {id:'run'}; },
  exploreStatus: async () => {
    probe.reads++;
    if (run?.state === 'collecting' && ++reads >= 3) run = { ...run, state:'ready', model:'fallback', recommendation } as ExploreStatus;
    return run;
  },
  cancelExplore: async () => { probe.calls.push({key:'cancel'}); if(run) run = {...run,state:'cancelled'}; return {cancelled:true}; },
  personalSettings: async()=>snapshot.personal, larkChannel:async()=>snapshot.lark,
  briefSettings:async()=>snapshot.brief, pushSettings:async()=>snapshot.push,
  proactiveSettings:async()=>({settings:snapshot.proactive}), personalProfile:async()=>snapshot.profile,
  searchLark:async()=>[{id:'search-result',name:'Search result',hint:'Found in directory'}],
  savePersonalSettings:async v=>save('personal',v), saveLarkChannel:async v=>save('lark',v),
  saveBriefSettings:async v=>save('brief',v), savePushSettings:async v=>save('push',v),
  saveProactiveSettings:async v=>save('proactive',v), savePersonalProfile:async (text,rev)=>save('profile',{text,rev}),
} as Api;
function App() {
  const [open,setOpen] = useState(true);
  return open ? <ExploreDialog api={api} onClose={()=>{probe.closed=true;setOpen(false);}} onApplied={(draft,saved)=>{probe.applied={draft,saved};setOpen(false);}} /> : null;
}
createRoot(document.getElementById('root')!).render(<App />);
