/** Framework-independent picker. The frame cannot save or send messages; its parent owns those actions. */
export const PREVIEW_PICKER = String.raw`(() => {
  if (window.parent === window || window.__calliPreviewPicker) return;
  window.__calliPreviewPicker = true;
  let enabled = false, origin = null;
  const outline = document.createElement('div');
  outline.setAttribute('aria-hidden', 'true');
  Object.assign(outline.style, { position:'fixed', pointerEvents:'none', zIndex:'2147483647', border:'1px solid currentColor', display:'none', boxSizing:'border-box' });
  document.documentElement.append(outline);
  const send = data => { if (origin) window.parent.postMessage(data, origin); };
  const box = el => { const r=el.getBoundingClientRect(); Object.assign(outline.style,{left:r.x+'px',top:r.y+'px',width:r.width+'px',height:r.height+'px',display:'block'}); };
  function selector(el) {
    const parts=[];
    for (let node=el;node && node.nodeType===1;node=node.parentElement) {
      if(node.id){parts.unshift('#'+CSS.escape(node.id));break;}
      const stable=node.getAttribute('data-preview-id') || node.getAttribute('data-testid');
      if(stable){const attr=node.hasAttribute('data-preview-id')?'data-preview-id':'data-testid';parts.unshift('['+attr+'="'+CSS.escape(stable)+'"]');break;}
      let part=node.localName;const siblings=Array.from(node.parentElement?.children||[]).filter(s=>s.localName===part);
      if(siblings.length>1)part+=':nth-of-type('+(siblings.indexOf(node)+1)+')';parts.unshift(part);
    }
    return parts.join(' > ').slice(0,1500);
  }
  window.addEventListener('message', event => {
    if(event.source!==window.parent || !event.data || typeof event.data!=='object')return;
    if(event.data.type==='calli-preview:init'){origin=event.origin;outline.style.color=event.data.color || 'currentColor';send({type:'calli-preview:connected'});return;}
    if(!origin || event.origin!==origin)return;
    if(event.data.type==='calli-preview:pick'){enabled=event.data.enabled===true;outline.style.display='none';}
    if(event.data.type==='calli-preview:locate' && typeof event.data.selector==='string'){
      try{const el=document.querySelector(event.data.selector);if(el){el.scrollIntoView({block:'center'});box(el);}else send({type:'calli-preview:missing'});}catch{send({type:'calli-preview:missing'});}
    }
  });
  document.addEventListener('pointermove',event=>{if(enabled && event.pointerType==='mouse' && event.target instanceof Element)box(event.target.closest('button,a')||event.target);},true);
  for(const type of ['pointerdown','focus','pointerenter'])document.addEventListener(type,event=>{if(enabled){event.stopImmediatePropagation();if(type==='pointerdown')event.preventDefault();}},true);
  document.addEventListener('click',event=>{
    if(!enabled || !(event.target instanceof Element))return;
    event.preventDefault();event.stopImmediatePropagation();enabled=false;
    const el=event.target.closest('button,a')||event.target,r=el.getBoundingClientRect();box(el);
    const url=new URL(location.href);url.search='';
    const variant=document.querySelector('[data-variant]')?.getAttribute('data-variant')||'';
    send({type:'calli-preview:picked',target:{selector:selector(el),text:(el.textContent||'').trim().slice(0,300),html:el.outerHTML.slice(0,6000),rect:{x:r.x,y:r.y,width:r.width,height:r.height}},context:{title:document.title.slice(0,300),url:url.href.slice(0,4096),theme:document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'),variant:variant.slice(0,100),revision:document.documentElement.dataset.previewRevision||'',viewport:{width:innerWidth,height:innerHeight}}});
  },true);
  document.addEventListener('keydown',event=>{if(event.key==='Escape' && enabled){enabled=false;outline.style.display='none';send({type:'calli-preview:cancel'});}},true);
  window.parent.postMessage({type:'calli-preview:ready'},'*');
})();`;
