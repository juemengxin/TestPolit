export const kinds:Record<string,any>={
 requirement:{name:'需求文档',icon:'Document',content:true,config:{status:'draft',owner:'',tags:[]}},
 knowledge:{name:'知识库',icon:'Collection',content:true,config:{embedding_model:null,reranker_model:null,chunk_size:700}},
 knowledge_document:{name:'知识文档',icon:'Document',content:true,config:{}},
 prompt:{name:'Prompt 模板',icon:'MagicStick',content:true,config:{variables:['requirement','context']}},
 case_template:{name:'用例模板',icon:'Tickets',content:true,config:{required_fields:['name','preconditions','steps','expected'],priority:'P1'}},
 strategy:{name:'生成策略',icon:'SetUp',content:true,config:{coverage:['正常路径','边界条件','异常路径','权限'],rounds:2}},
 module:{name:'功能模块',icon:'Folder',content:true,config:{}},
 api:{name:'接口用例',icon:'Connection',config:{method:'GET',url:'/health',headers:{},params:{},body:{},assertions:[{type:'status',expected:200}],extract:{},before:[],after:[]}},
 environment:{name:'接口环境',icon:'Setting',config:{base_url:'http://127.0.0.1:8000',variables:{},headers:{}}},
 database:{name:'数据库配置',icon:'Coin',config:{driver:'sqlite',path:'test.sqlite3'}},
 function:{name:'公共函数',icon:'Operation',config:{before:[{op:'uuid',name:'request_id'}],after:[]}},
 tag:{name:'标签',icon:'PriceTag',config:{color:'#6569e7'}},
 ui_case:{name:'UI 用例',icon:'Monitor',config:{platform:'web',base_url:'http://127.0.0.1:5173',steps:[{action:'goto',value:'/'},{action:'assert_text',selector:'h2',value:'登录工作空间'}]}},
 ui_flow:{name:'测试流程',icon:'SetUp',config:{platform:'web',status:'draft',case_ids:[],environment_id:null}},
 machine:{name:'执行机',icon:'Monitor',config:{executor:'local-playwright-chromium',description:'当前 Worker 所在主机',enabled:true}},
 ui_environment:{name:'UI 环境',icon:'Setting',config:{base_url:'http://127.0.0.1:5173',timeout:10000,variables:{}}},
 page:{name:'页面对象',icon:'CopyDocument',config:{url:'/login',elements:{username:'#login-username',password:'#login-password',submit:'button[type=submit]'}}},
 ui_data:{name:'公共数据',icon:'Files',config:{variables:{username:'demo'}}},
 skill_source:{name:'技能来源',icon:'Shop',content:true,config:{url:'',manifest:{name:'Playwright 测试设计',instructions:'使用稳定的语义定位，显式断言页面状态，避免固定等待。'}}},
 conversation:{name:'对话',icon:'ChatDotRound',content:true,config:{}}
}
export const sections:Record<string,any>={
 requirements:{title:'需求管理',eyebrow:'REQUIREMENTS & REVIEW',description:'从需求导入到智能评审，让每一个测试都有清晰的依据。',tabs:['requirement']},
 knowledge:{title:'知识库',eyebrow:'KNOWLEDGE & RETRIEVAL',description:'汇集产品文档和业务规则，为智能生成提供可追溯的上下文。',tabs:['knowledge','knowledge_document']},
 'api-testing':{title:'接口用例与配置',eyebrow:'API AUTOMATION',description:'管理接口契约、模块、运行环境与公共函数；在接口工作台中编辑与调试请求。',tabs:['api','module','environment','database','function','tag']},
 'ui-testing':{title:'UI 自动化',eyebrow:'BROWSER AUTOMATION',description:'组织 App 与 Web 用例和测试流程，通过自动化执行保留截图、视频和 Trace。',tabs:['ui_case','ui_flow','module','machine','ui_environment','page','ui_data']},
 templates:{title:'模板与策略',eyebrow:'TEAM STANDARDS',description:'沉淀提示词、用例结构和生成策略，统一团队测试规范。',tabs:['prompt','case_template','strategy']},
 'skill-library':{title:'Skills 技能库',eyebrow:'SPECIALIZED CAPABILITIES',description:'从受信任的清单安装声明式技能，并将专项指令关联到 Agent。',tabs:['skill_source']}
}
export const jobNames:Record<string,string>={triage:'失败分诊',generate:'智能生成',design:'需求拆解与模块用例',optimize:'用例优化',review:'需求评审',split:'需求拆分',analyze:'失败分析',chat:'Agent 对话',ingest:'文档解析与索引',api:'接口执行',ui:'UI 执行',mcp:'MCP 工具调用',connection:'连接测试',embed_test:'嵌入测试',rerank_test:'重排测试',app_video:'App 录屏识别',decision:'结构化决策'}
