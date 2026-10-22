import { createApp } from 'vue'
import { createPinia } from 'pinia'
import {
  ElAlert,
  ElButton,
  ElCheckbox,
  ElCollapse,
  ElCollapseItem,
  ElConfigProvider,
  ElDialog,
  ElDrawer,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElIcon,
  ElImage,
  ElInput,
  ElInputNumber,
  ElOption,
  ElPagination,
  ElProgress,
  ElRadioButton,
  ElRadioGroup,
  ElSelect,
  ElSwitch,
  ElTabPane,
  ElTable,
  ElTableColumn,
  ElTabs,
  ElTag,
  ElTimeline,
  ElTimelineItem,
  ElTree,
} from 'element-plus'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import {t} from './i18n'
import router from './router'
const app=createApp(App)
app.config.globalProperties.$t=t
app.use(createPinia()).use(router)

// Register only components referenced by the workspace so unused Element Plus
// features can be removed from the initial application bundle.
const workspaceComponents = [
  ElAlert, ElButton, ElCheckbox, ElCollapse, ElCollapseItem, ElConfigProvider,
  ElDialog, ElDrawer, ElDropdown, ElDropdownItem, ElDropdownMenu, ElEmpty,
  ElForm, ElFormItem, ElIcon, ElImage, ElInput, ElInputNumber, ElOption,
  ElPagination, ElProgress, ElRadioButton, ElRadioGroup, ElSelect, ElSwitch,
  ElTabPane, ElTable, ElTableColumn, ElTabs, ElTag, ElTimeline,
  ElTimelineItem, ElTree,
]
for (const component of workspaceComponents) {
  if (component.name) app.component(component.name, component)
}

app.mount('#app')
