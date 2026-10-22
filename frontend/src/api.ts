import axios from 'axios'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from './errors'
export const api = axios.create({ baseURL: '/api/', xsrfCookieName:'csrftoken', xsrfHeaderName:'X-CSRFToken', withCredentials:true })
api.interceptors.response.use(r=>r, error=>{
  if (error.response?.status === 401) {
    window.dispatchEvent(new CustomEvent('testpilot:auth-expired'))
    ElMessage.error('身份认证信息未提供或登录已过期，请重新登录。')
    return Promise.reject(error)
  }
  const message = apiErrorMessage(error, '无法连接服务，请确认后端已启动')
  ElMessage.error(message.slice(0,240))
  return Promise.reject(error)
})
export async function allPages(url:string, params:Record<string,unknown>={}) {
  const result:any[]=[]; let page=1
  while (true) {const {data}=await api.get(url,{params:{...params,page}}); result.push(...data.results); if(!data.next) return result; page++}
}
export const fmt = (value?:string) => value ? new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '—'
export const stateNames:Record<string,string>={awaiting_approval:'待审批',queued:'排队中',running:'执行中',completed:'已完成',failed:'失败',cancelled:'已取消',draft:'草稿',ready:'就绪',archived:'已归档',passed:'通过',skipped:'跳过'}
