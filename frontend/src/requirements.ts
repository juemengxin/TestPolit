export const requirementStates:Record<string,string>={draft:'草稿',parsing:'解析中',ready:'待评审',reviewing:'评审中',approved:'已确认',archived:'已归档'}
export interface Requirement {id:number;name:string;parent:number|null;content:string;config:Record<string,any>;version:number;updated_at:string}
export interface RequirementNode {id:number;label:string;item:Requirement;children:RequirementNode[];matched:boolean}
export function requirementTree(items:Requirement[],search='',status='',priority=''){
  const query=search.trim().toLowerCase(),nodes=new Map<number,RequirementNode>()
  for(const item of items){
    const text=[item.name,item.config.owner,...(Array.isArray(item.config.tags)?item.config.tags:[])].join(' ').toLowerCase()
    nodes.set(item.id,{id:item.id,label:item.name,item,children:[],matched:text.includes(query)&&(!status||(item.config.status||'draft')===status)&&(!priority||(item.config.priority||'P1')===priority)})
  }
  const roots:RequirementNode[]=[]
  for(const node of nodes.values()){
    const visited=new Set([node.id]);let parent=node.item.parent,cycle=false
    while(parent!==null&&nodes.has(parent)){if(visited.has(parent)){cycle=true;break}visited.add(parent);parent=nodes.get(parent)!.item.parent}
    if(!cycle&&node.item.parent!==null&&nodes.has(node.item.parent))nodes.get(node.item.parent)!.children.push(node)
    else roots.push(node)
  }
  const prune=(node:RequirementNode):RequirementNode|null=>{
    node.children=node.children.map(prune).filter((child):child is RequirementNode=>!!child)
    return node.matched||node.children.length?node:null
  }
  return roots.map(prune).filter((node):node is RequirementNode=>!!node)
}
export function parentCandidates(items:Requirement[],id?:number){
  return items.filter(item=>{
    const visited=new Set<number>();let cursor:Requirement|undefined=item
    while(cursor){if(cursor.id===id||visited.has(cursor.id))return false;visited.add(cursor.id);cursor=items.find(parent=>parent.id===cursor!.parent)}
    return true
  })
}
