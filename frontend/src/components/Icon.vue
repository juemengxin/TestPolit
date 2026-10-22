<script setup lang="ts">
import type { Component } from 'vue'
import { computed } from 'vue'

import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ChatDotRound,
  Coin,
  Collection,
  Connection,
  CopyDocument,
  Cpu,
  DataAnalysis,
  Delete,
  Document,
  DocumentChecked,
  Download,
  Files,
  Folder,
  FolderAdd,
  FolderOpened,
  Grid,
  InfoFilled,
  Link,
  Lock,
  MagicStick,
  Monitor,
  OfficeBuilding,
  Operation,
  Paperclip,
  Plus,
  PriceTag,
  Refresh,
  Right,
  Search,
  SetUp,
  Setting,
  Shop,
  SuccessFilled,
  Tickets,
  Upload,
  VideoPlay,
  WarningFilled,
} from '@element-plus/icons-vue'

const props=defineProps<{name:string,size?:number}>()
const iconComponents: Record<string, Component> = {
  ArrowDown, ArrowLeft, ArrowRight, ChatDotRound, Coin, Collection,
  Connection, CopyDocument, Cpu, DataAnalysis, Delete, Document,
  DocumentChecked, Download, Files, Folder, FolderAdd, FolderOpened,
  Grid, InfoFilled, Link, Lock, MagicStick, Monitor, OfficeBuilding,
  Operation, Paperclip, Plus, PriceTag, Refresh, Right, Search, SetUp,
  Setting, Shop, SuccessFilled, Tickets, Upload, VideoPlay, WarningFilled,
}
const component=computed(()=>iconComponents[props.name]||Grid)
</script>
<template><el-icon :size="size||19"><component :is="component"/></el-icon></template>
