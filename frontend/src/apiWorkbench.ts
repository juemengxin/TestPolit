export interface WorkspaceEntity {id:number;name:string;parent:number|null;config:Record<string,any>}
export interface RequestNode {key:string;label:string;item?:WorkspaceEntity;children?:RequestNode[];count:number}

export function buildRequestTree(modules:WorkspaceEntity[],requests:WorkspaceEntity[],query='',selected?:number){
  const folders=new Map(modules.map(folder=>[folder.id,folder]))
  const ancestors=(parent:number|null)=>{
    const chain:WorkspaceEntity[]=[],seen=new Set<number>()
    while(parent!==null&&folders.has(parent)&&!seen.has(parent)){
      seen.add(parent);const folder=folders.get(parent)!;chain.push(folder);parent=folder.parent
    }
    return chain
  }
  const search=query.trim().toLowerCase()
  const filtered=requests.filter(item=>{
    const chain=ancestors(item.parent)
    const inModule=selected===undefined||(selected===-1?!folders.has(item.parent!):chain.some(folder=>folder.id===selected))
    return inModule&&[item.name,item.config.method,item.config.url,...chain.map(folder=>folder.name)].join(' ').toLowerCase().includes(search)
  })
  const nodes=new Map<number,RequestNode>(modules.map(folder=>[folder.id,{key:`module-${folder.id}`,label:folder.name,children:[],count:0}]))
  const roots:RequestNode[]=[],ungrouped:RequestNode={key:'ungrouped',label:'未分组',children:[],count:0}
  for(const folder of modules){
    const node=nodes.get(folder.id)!
    const parent=folder.parent===null?undefined:nodes.get(folder.parent)
    if(parent&&!ancestors(folder.parent).some(ancestor=>ancestor.id===folder.id))parent.children!.push(node)
    else roots.push(node)
  }
  for(const item of filtered){
    const parent=nodes.get(item.parent!)||ungrouped
    parent.children!.push({key:`api-${item.id}`,label:item.name,item,count:1})
  }
  if(ungrouped.children!.length)roots.push(ungrouped)
  const prune=(node:RequestNode):RequestNode|null=>{
    if(node.item)return node
    node.children=node.children!.map(prune).filter((child):child is RequestNode=>child!==null)
    node.count=node.children.reduce((sum,child)=>sum+child.count,0)
    return node.count||(!search&&selected===undefined)?node:null
  }
  return {nodes:roots.map(prune).filter((node):node is RequestNode=>node!==null),count:filtered.length}
}

export function responseOutcome(job:any){
  if(!job)return {label:'尚未执行',type:'info' as const}
  if(job.status==='failed')return {label:'执行失败',type:'danger' as const}
  if(job.status==='cancelled')return {label:'已取消',type:'info' as const}
  if(job.status!=='completed')return {label:({queued:'排队中',running:'执行中',awaiting_approval:'待审批'} as Record<string,string>)[job.status]||job.status,type:'info' as const}
  if(job.mode==='simulation')return {label:'模拟完成 · 未发送请求',type:'info' as const}
  const result=job.result?.cases?.[0]
  if(result?.status==='passed')return {label:'断言通过',type:'success' as const}
  if(result?.status==='failed')return {label:result.response?'验证失败':'请求失败',type:'danger' as const}
  return {label:result?.status==='skipped'?'已跳过':'任务完成 · 无验证结果',type:'info' as const}
}
