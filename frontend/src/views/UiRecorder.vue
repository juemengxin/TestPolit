<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppRecorder from '../components/AppRecorder.vue'
import WebRecorder from '../components/WebRecorder.vue'
const route = useRoute(), router = useRouter()
const platform = computed(() => route.query.platform === 'app' ? 'app' : 'web')
function select(value: string | number | boolean | undefined) { router.replace({path:'/ui-testing/record',query:{platform:String(value)}}) }
</script>
<template><div><div class="recorder-platform"><el-button @click="router.push('/ui-testing?platform='+platform)">返回 UI 自动化</el-button><el-radio-group :model-value="platform" @change="select"><el-radio-button value="web">Web 录制</el-radio-button><el-radio-button value="app">App 录制</el-radio-button></el-radio-group></div><AppRecorder v-if="platform==='app'"/><WebRecorder v-else/></div></template>
<style scoped>.recorder-platform{display:flex;align-items:center;gap:24px;max-width:1180px;margin:0 auto 20px}</style>
