<script setup lang="ts">
interface TriageItem {
  case_id: number
  name: string
  category: string
  label: string
  source: string
  confidence: number | null
  laya_answer?: {label:string; confidence:number; threshold:number; accepted:boolean}
  recommendation: string
  evidence: {signals: string[]; status_code: number | null}
}
defineProps<{data?: {status:string; service_status:string; notice:string; items:TriageItem[]; inference?: {checkpoint:string; duration_ms:number; response_sha256:string; usage:Record<string,number>}}}>()
const services:Record<string,string> = {
  local_rules:'本地规则（不调用外部模型）', not_needed:'没有失败用例，无需分诊',
  not_called:'模拟模式不进行分诊',
}
const signals:Record<string,string> = {
  connection_failure:'连接或白名单异常',request_timeout:'网络请求超时',missing_variable:'依赖变量缺失',
  invalid_assertion:'断言配置缺失或无效',locator_issue:'定位器异常',device_issue:'设备不可用',assertion_failed:'断言未满足',
}
</script>
<template>
  <section v-if="data" class="panel failure-triage">
    <div class="triage-heading"><h3>测试失败分诊</h3><el-tag type="info">{{services[data.service_status]||(data.inference?'历史模型结果':'本地规则（不调用外部模型）')}}</el-tag></div>
    <p class="helper">{{data.notice}}</p>
    <p v-if="data.inference" class="helper">此为历史模型分析记录 · 检查点 {{data.inference.checkpoint}} · 请求耗时 {{data.inference.duration_ms}} ms<span v-if="data.inference.usage.input_tokens"> · 输入 {{data.inference.usage.input_tokens}} Token</span>。新分诊仅使用本地规则。</p>
    <el-table v-if="data.items.length" :data="data.items" empty-text="暂无失败用例">
      <el-table-column label="用例" min-width="170"><template #default="{row}">#{{row.case_id}} · {{row.name}}</template></el-table-column>
      <el-table-column label="处理方向" width="145"><template #default="{row}"><el-tag :type="row.category==='unknown'?'info':'warning'">{{row.label}}</el-tag></template></el-table-column>
      <el-table-column label="依据" min-width="160"><template #default="{row}"><div v-for="signal in row.evidence.signals" :key="signal">{{signals[signal]||signal}}</div><div v-if="row.evidence.status_code">HTTP {{row.evidence.status_code}}</div><span v-if="!row.evidence.signals.length&&!row.evidence.status_code">证据不足</span></template></el-table-column>
      <el-table-column label="来源" width="130"><template #default="{row}">{{row.source==='laya'?'历史模型':'本地规则'}}<div v-if="row.laya_answer">历史建议未采纳：{{row.laya_answer.label}} · {{(row.laya_answer.confidence*100).toFixed(1)}}%</div></template></el-table-column>
      <el-table-column prop="recommendation" label="建议（需核对）" min-width="240"/>
    </el-table>
    <p v-else class="helper">{{data.status==='simulation'?'模拟结果不进行真实分诊。':'本次没有失败用例。'}}</p>
  </section>
</template>
<style scoped>
.failure-triage{padding:20px;margin:16px 0}.triage-heading{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:12px}.triage-heading h3{margin:0}.failure-triage .helper{margin:12px 0;line-height:1.7}
</style>
