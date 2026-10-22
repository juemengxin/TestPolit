import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api, allPages } from './api'
export const useWorkspace = defineStore('workspace',()=>{
 const user=ref<any>(null), projects=ref<any[]>([]), projectId=ref<number|null>(null), initialized=ref(false)
 const project=computed(()=>projects.value.find(p=>p.id===projectId.value))
 const canEdit=computed(()=>['owner','editor'].includes(project.value?.role))
 async function loadProjects(){projects.value=await allPages('projects/');if(!projects.value.some(p=>p.id===projectId.value))projectId.value=projects.value[0]?.id??null}
 async function init(){const {data}=await api.get('auth/session/');user.value=data.user;if(user.value)await loadProjects();initialized.value=true}
 return {user,projects,projectId,project,canEdit,initialized,loadProjects,init}
})
