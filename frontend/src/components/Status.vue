<script setup lang="ts">
import {stateNames} from '../api'
defineProps<{value:string}>()
</script>
<template><span class="status" :class="value"><i></i>{{$t(stateNames[value]||value)}}</span></template>
