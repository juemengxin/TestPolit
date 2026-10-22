import test from 'node:test'
import assert from 'node:assert/strict'
import {buildRequestTree,responseOutcome} from '../src/apiWorkbench.ts'

const modules=[{id:1,name:'订单',parent:null,config:{}},{id:2,name:'支付',parent:1,config:{}},{id:3,name:'空模块',parent:null,config:{}}]
const requests=[{id:10,name:'查询订单',parent:1,config:{method:'GET',url:'/orders'}},{id:11,name:'创建支付',parent:2,config:{method:'POST',url:'/payments'}},{id:12,name:'健康检查',parent:null,config:{method:'GET',url:'/health'}}]

test('module tree includes nested counts and empty modules',()=>{
  const result=buildRequestTree(modules,requests)
  assert.equal(result.count,3)
  assert.equal(result.nodes[0].count,2)
  assert.equal(result.nodes[0].children[0].label,'支付')
  assert.equal(result.nodes[0].children[0].children[0].item.id,11)
  assert.equal(result.nodes[1].label,'空模块')
  assert.equal(result.nodes[2].label,'未分组')
})
test('selecting a parent includes descendant APIs',()=>{
  const result=buildRequestTree(modules,requests,'',1)
  assert.equal(result.count,2)
  assert.equal(result.nodes.length,1)
  assert.equal(buildRequestTree(modules,requests,'',2).count,1)
})
test('search matches module ancestors, methods and paths',()=>{
  assert.equal(buildRequestTree(modules,requests,' 订单 ').count,2)
  assert.equal(buildRequestTree(modules,requests,'post').count,1)
  assert.equal(buildRequestTree(modules,requests,'/health').nodes[0].key,'ungrouped')
  assert.deepEqual(buildRequestTree(modules,requests,'missing').nodes,[])
})
test('missing module references remain accessible in ungrouped',()=>{
  const result=buildRequestTree(modules,[{...requests[0],parent:99}],'',-1)
  assert.equal(result.count,1)
  assert.equal(result.nodes[0].key,'ungrouped')
})
test('malformed cyclic modules do not lose APIs or recurse forever',()=>{
  const result=buildRequestTree([{...modules[0],parent:2},modules[1]],requests)
  assert.equal(result.count,3)
  assert.equal(result.nodes.reduce((sum,node)=>sum+node.count,0),3)
})
test('completed job distinguishes assertion failure from request failure',()=>{
  const job={status:'completed',mode:'live',result:{cases:[{status:'failed',response:{status_code:200}}]}}
  assert.equal(responseOutcome(job).label,'验证失败')
  assert.equal(responseOutcome({...job,result:{cases:[{status:'failed'}]}}).label,'请求失败')
  assert.equal(responseOutcome({...job,result:{cases:[{status:'passed'}]}}).type,'success')
})
test('simulation and empty results never imply verified success',()=>{
  assert.equal(responseOutcome({status:'completed',mode:'simulation'}).label,'模拟完成 · 未发送请求')
  assert.equal(responseOutcome({status:'completed',mode:'live',result:{}}).type,'info')
  assert.equal(responseOutcome({status:'failed'}).type,'danger')
  assert.equal(responseOutcome({status:'queued'}).label,'排队中')
})
