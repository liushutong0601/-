"""给一次填写复核生成 DOM 变更凭据；后续只检查凭据，不反复切题。"""

_SETUP = r"""
let guard=window[key];
if(!guard){
  guard={serial:0,records:new Map()};
  const relevant=m=>{
    if(m.type!=='attributes')return true;
    // 组件宿主的显隐、位置由分页控制；其内部的显隐变化可能改变选项结构。
    if(['class','style','aria-hidden'].includes(m.attributeName)&&m.target.matches('[sgpulse-type="component"]'))return false;
    if(m.attributeName==='class'&&m.target.matches('.component__widget'))return false;
    return m.oldValue!==m.target.getAttribute(m.attributeName);
  };
  guard.roots=r=>{
    const roots=[r.node];
    for(let i=0;i<roots.length;i++){
      const root=roots[i];
      if(!r.roots.has(root)){
        r.roots.add(root);r.observer.observe(root,{subtree:true,childList:true,characterData:true,
          attributes:true,attributeOldValue:true,attributeFilter:['aria-checked','aria-selected','class','role','modelid',
            'src','srcset','hidden','aria-hidden','style','aria-setsize','data-id','data-itemindex']});
      }
      if(root.shadowRoot)roots.push(root.shadowRoot);
      for(const el of root.querySelectorAll('*'))if(el.shadowRoot)roots.push(el.shadowRoot);
    }
  };
  guard.flush=r=>{
    if(r.observer.takeRecords().some(relevant))r.revision++;
    guard.roots(r);
  };
  guard.watch=node=>{
    const id=node.getAttribute('modelid');let r=guard.records.get(id);
    if(!r||r.node!==node){
      if(r)r.observer.disconnect();
      r={node,serial:++guard.serial,revision:0,roots:new Set()};
      r.observer=new MutationObserver(ms=>{if(ms.some(relevant))r.revision++;guard.roots(r);});
      guard.records.set(id,r);guard.roots(r);
    }
    guard.flush(r);return r;
  };
  Object.defineProperty(window,key,{value:guard,configurable:true});
}
"""


def guarded_reader(reader):
    return '(el,key)=>{' + _SETUP + 'const r=guard.watch(el);const raw=('+reader+')(el);return {...raw,verification:[r.serial,r.revision]};}'


VALIDATE = r"""({key,stamps})=>{
  const g=window[key];if(!g)return false;
  return stamps.every(([id,serial,revision])=>{
    const r=g.records.get(id);
    if(!r||!r.node.isConnected||r.node.getAttribute('modelid')!==id)return false;
    g.flush(r);return r.serial===serial&&r.revision===revision;
  });
}"""

CLOSE = r"""key=>{
  const g=window[key];if(g){for(const r of g.records.values())r.observer.disconnect();delete window[key];}
}"""
