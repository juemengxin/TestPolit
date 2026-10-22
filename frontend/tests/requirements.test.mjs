import test from 'node:test'
import assert from 'node:assert/strict'
import {requirementTree,parentCandidates} from '../src/requirements.ts'

const records=[
  {id:1,name:'产品需求',parent:null,content:'',version:1,updated_at:'',config:{}},
  {id:2,name:'登录',parent:1,content:'',version:1,updated_at:'',config:{status:'ready',priority:'P0',owner:'李明',tags:['账户']}},
  {id:3,name:'密码',parent:2,content:'',version:1,updated_at:'',config:{status:'approved',priority:'P1'}}
]
test('requirement filters retain ancestors as context',()=>{
  const result=requirementTree(records,'账户','ready','P0')
  assert.equal(result.length,1)
  assert.equal(result[0].matched,false)
  assert.equal(result[0].children[0].id,2)
  assert.equal(result[0].children[0].children.length,0)
  assert.equal(requirementTree(records,'李明')[0].children[0].id,2)
})
test('parent choices exclude self and descendants',()=>{
  assert.deepEqual(parentCandidates(records,1),[])
  assert.deepEqual(parentCandidates(records,2).map(item=>item.id),[1])
  assert.equal(parentCandidates(records).length,3)
})
test('missing parents and cyclic legacy data remain safe',()=>{
  assert.equal(requirementTree([{...records[0],parent:99}])[0].id,1)
  assert.equal(requirementTree([{...records[0],parent:2},records[1]]).length,2)
  assert.equal(parentCandidates([{...records[0],parent:2},records[1]]).length,0)
})
