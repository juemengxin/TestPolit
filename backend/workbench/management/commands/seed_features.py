from django.core.management.base import BaseCommand,CommandError
from django.contrib.auth import get_user_model
from workbench.models import Project,Entity,Asset,KnowledgeChunk
from workbench.entities import save_revision,hash_vector
class Command(BaseCommand):
    help='Add editable v2 examples without overwriting existing records'
    def add_arguments(self,parser):parser.add_argument('--username',required=True)
    def handle(self,*args,**options):
        user=get_user_model().objects.filter(username=options['username']).first()
        if not user:raise CommandError('User not found')
        project=Project.objects.filter(owner=user).first()
        if not project:raise CommandError('Create a project first')
        def add(kind,name,content='',config=None,parent=None):
            item,created=Entity.objects.get_or_create(project=project,kind=kind,name=name,defaults={'content':content,'config':config or {},'parent':parent,'created_by':user})
            if created:save_revision(item,user)
            return item
        requirement=add('requirement','用户登录与会话管理需求','1. 用户使用账号和密码登录。\n2. 连续 5 次密码错误后锁定 15 分钟。\n3. 登录会话 8 小时后失效。\n4. 观察者只能查看测试记录，不得修改或执行。\n5. 注销后立即清除当前会话。\n\n验收标准：正确凭据进入工作台；错误凭据返回统一提示，不泄露账号是否存在。',{'status':'ready','tags':['用户中心','权限']})
        kb=add('knowledge','团队测试规范','产品与测试规范知识库',{'embedding_model':None,'reranker_model':None})
        doc=add('knowledge_document','账号与权限测试规范','测试用户登录时应覆盖：正常凭据、错误密码、账号停用、会话过期和注销。项目角色分为所有者、编辑者和观察者。跨项目请求必须返回拒绝，导出与附件下载也需要校验权限。',parent=kb)
        if not doc.chunks.exists():KnowledgeChunk.objects.create(document=doc,index=0,text=doc.content,vector=hash_vector(doc.content))
        module=add('module','用户中心');add('module','交易中心');add('module','支付中心')
        for case in Asset.objects.filter(project=project,kind='case'):
            if '登录' in case.name and not case.data.get('module_id'):case.data.update({'module_id':module.pk,'type':'functional','preconditions':'测试账号已创建，环境可访问'});case.save()
        add('prompt','企业测试设计助手','按正常、边界、异常、权限四个维度设计用例。每条用例必须具有明确前置条件、可执行步骤和可观测的预期结果。不得将未知业务规则编造成事实。',{'variables':['requirement','context']})
        add('case_template','标准功能测试用例','包含名称、优先级、类型、前置条件、步骤、预期结果和备注。',{'required_fields':['name','priority','preconditions','steps']})
        add('strategy','核心链路优先','优先覆盖高风险业务链路与权限边界，标记尚未明确的验收条件。',{'coverage':['正常路径','边界','异常','权限'],'rounds':2})
        add('environment','本地验证环境',config={'base_url':'http://127.0.0.1:8766','variables':{},'headers':{'X-Test-Client':'TestPilot'}})
        add('api','本地 API 健康检查',config={'method':'GET','url':'/health','headers':{},'params':{},'body':{},'assertions':[{'type':'status','expected':200},{'type':'json_equals','path':'status','expected':'ok'}],'extract':{'service':'service'},'before':[],'after':[]})
        add('ui_environment','本地浏览器验证环境',config={'base_url':'http://127.0.0.1:8766','timeout':10000,'variables':{}})
        add('ui_case','本地页面交互验证',config={'base_url':'http://127.0.0.1:8766','steps':[{'action':'goto','value':'/'},{'action':'assert_text','selector':'h1','value':'TestPilot Fixture'},{'action':'fill','selector':'#name','value':'TestPilot'},{'action':'click','selector':'#submit'},{'action':'assert_text','selector':'#result','value':'Hello TestPilot'}]})
        add('machine','本地 Chromium Worker',config={'executor':'local-playwright-chromium','enabled':True})
        add('page','本地验证页面',config={'url':'/','elements':{'name':'#name','submit':'#submit','result':'#result'}})
        add('ui_data','通用测试数据',config={'variables':{'username':'demo'}})
        add('skill_source','团队内置技能来源','可信声明式技能示例，不执行安装脚本。',{'manifest':{'name':'Playwright 稳定性设计','instructions':'优先使用稳定定位与状态断言。根据 Trace 和截图定位第一个失败步骤，提出修复建议，不盲目扩大等待时间。'}})
        suite,created=Asset.objects.get_or_create(project=project,kind='suite',name='用户中心基础套件',defaults={'status':'ready','created_by':user,'data':{'case_ids':list(Asset.objects.filter(project=project,kind='case',status='ready').values_list('id',flat=True)[:3])}})
        self.stdout.write('V2 feature examples created; external endpoints point only at the optional local fixture on port 8766.')
