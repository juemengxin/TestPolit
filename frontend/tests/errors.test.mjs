import test from 'node:test'
import assert from 'node:assert/strict'
import { apiErrorMessage } from '../src/errors.ts'

test('uses a non-empty string detail from an API error', () => {
  assert.equal(apiErrorMessage({ response: { data: { detail: '目标不在白名单' } } }, '请求失败'), '目标不在白名单')
})

test('falls back for malformed, empty, or non-API errors', () => {
  assert.equal(apiErrorMessage({ response: { data: { detail: '  ' } } }, '请求失败'), '请求失败')
  assert.equal(apiErrorMessage({ response: { data: { detail: { message: 'bad' } } } }, '请求失败'), 'detail: message: bad')
  assert.equal(apiErrorMessage(new Error('network failure'), '请求失败'), '请求失败')
  assert.equal(apiErrorMessage(null, '请求失败'), '请求失败')
})

test('formats validation errors and supports a preferred nested field', () => {
  const error = { response: { data: { detail: { config: '配置无效', steps: ['步骤缺失'] } } } }
  assert.equal(apiErrorMessage(error, '请求失败', ['detail', 'config']), '配置无效')
  assert.equal(apiErrorMessage(error, '请求失败'), 'detail: config: 配置无效；steps: 步骤缺失')
  assert.equal(apiErrorMessage({ response: { data: { name: ['必填'] } } }, '请求失败'), 'name: 必填')
})
