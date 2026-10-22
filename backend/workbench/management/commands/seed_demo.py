from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.utils import timezone
from workbench.models import Project, Resource, Asset, Run
from workbench.worker import claim, execute
class Command(BaseCommand):
    help = 'Seed explicitly labeled simulation example assets for an existing user (no default password)'
    def add_arguments(self, parser): parser.add_argument('--username',required=True)
    def handle(self,*args,**options):
        user = get_user_model().objects.filter(username=options['username']).first()
        if not user: raise CommandError('Create the user first using createsuperuser or createuser')
        project,created = Project.objects.get_or_create(key='MALL',defaults={'name':'电商平台测试项目','description':'覆盖用户、商品、订单与支付链路的测试工作空间。所有示例执行均为模拟。','owner':user})
        if not created:
            self.stdout.write('Demo project already exists; existing data untouched.')
            return
        configs=[('model','通用语言模型','用于理解测试需求与辅助用例设计',{'model_name':'demo-language-model'}),('model','视觉理解模型','预留界面截图分析能力',{'model_name':'demo-vision-model'}),('skill','测试用例设计','将业务需求转化为结构化测试步骤',{'instructions':'分析边界条件和异常路径，生成可复用测试用例。'}),('skill','缺陷分析助手','整理执行结果与问题线索',{'instructions':'根据测试日志归纳问题，提供排查方向。'}),('mcp','浏览器自动化服务','预留浏览器操作与页面验证能力',{'transport':'http'}),('mcp','接口测试服务','预留 REST 接口验证与响应分析能力',{'transport':'http'})]
        resources=[]
        for kind,name,desc,config in configs: resources.append(Resource.objects.create(project=project,kind=kind,name=name,description=desc+'（模拟配置）',settings=config))
        Resource.objects.create(project=project,kind='agent',name='端到端测试 Agent',description='整合模型与浏览器工具，规划核心业务链路验证（模拟配置）',settings={'instructions':'按测试计划依次验证关键用户旅程。','resource_ids':[resources[0].id,resources[2].id,resources[4].id]})
        Resource.objects.create(project=project,kind='agent',name='接口质量 Agent',description='面向 API 的异常路径与返回结果检查（模拟配置）',settings={'instructions':'检查接口契约和边界条件。','resource_ids':[resources[0].id,resources[5].id]})
        specs=[('账号密码登录成功','用户中心','P0','passed'),('错误密码登录校验','用户中心','P1','passed'),('商品搜索与筛选','商品中心','P1','passed'),('商品详情信息展示','商品中心','P2','passed'),('加入购物车','交易中心','P0','passed'),('购物车数量更新','交易中心','P1','passed'),('订单创建与库存校验','交易中心','P0','failed'),('优惠券使用规则','营销中心','P1','passed'),('订单支付状态同步','支付中心','P0','passed'),('订单取消与退款','支付中心','P1','skipped'),('收货地址管理','用户中心','P2','passed'),('个人资料更新','用户中心','P2','passed')]
        cases=[]
        for name,module,priority,result in specs:
            cases.append(Asset.objects.create(project=project,kind='case',name=name,description=f'{module}核心功能验证。示例资产，用于演示平台流程。',status='ready',priority=priority,created_by=user,data={'steps':[{'action':f'进入{module}，准备测试数据','expected':'页面可访问，数据已准备'},{'action':f'执行「{name}」操作','expected':'操作返回与业务规则一致的结果'},{'action':'检查界面提示与数据状态','expected':'状态与预期一致，无重复记录'}],'simulate_result':result}))
        plan=Asset.objects.create(project=project,kind='plan',name='核心交易链路 · 回归测试',description='覆盖登录、购物车、下单和支付的核心回归范围。',status='ready',priority='P0',created_by=user,data={'case_ids':[c.id for c in cases]})
        smoke=Asset.objects.create(project=project,kind='plan',name='用户中心 · 冒烟测试',description='验证登录与账户基本功能。',status='ready',created_by=user,data={'case_ids':[c.id for c in cases[:4]]})
        Asset.objects.create(project=project,kind='plan',name='支付模块 · 专项测试',description='支付链路与异常处理验证。',status='draft',created_by=user,data={'case_ids':[c.id for c in cases[8:10]]})
        Asset.objects.create(project=project,kind='flow',name='用户下单全链路',description='按顺序验证从登录到支付的用户旅程。',status='ready',created_by=user,data={'case_ids':[cases[i].id for i in [0,2,4,6,8]]})
        Asset.objects.create(project=project,kind='document',name='电商平台测试策略 v1.0',description='测试范围、优先级与验收标准',status='ready',created_by=user,data={'content':'测试目标\n保障用户、商品和交易核心链路的稳定性。\n\n测试范围\n1. 用户登录与账户信息\n2. 商品搜索、详情与购物车\n3. 订单、库存、支付与退款\n\n执行说明\n本项目为模拟示例。执行结果取自各用例的预设模拟结果，不代表真实系统质量。'})
        Asset.objects.create(project=project,kind='document',name='接口验证规范',description='接口测试设计参考',status='draft',created_by=user,data={'content':'关注输入边界、权限校验、幂等性和响应结构。实际服务适配器接入前，所有执行均为模拟。'})
        for day_offset in [6,5,4,3,2,1,0]:
            source=plan if day_offset%2==0 else smoke
            ids=source.data['case_ids']; selected=[c for c in cases if c.id in ids]
            run=Run.objects.create(project=project,source=source,name=source.name,created_by=user,snapshot=[{'id':c.id,'name':c.name,'priority':c.priority,'data':c.data} for c in selected],history=[])
            now=timezone.now()-timezone.timedelta(days=day_offset)
            # Claim only the freshly created demo job, never consume other projects' queues.
            run.worker_id='demo-seed';run.status='running';run.started_at=now;run.heartbeat_at=now
            run.history=[{'status':'running','at':now.isoformat(),'message':'示例历史记录 · 模拟执行'}];run.save()
            execute(run,delay=0)
            run.refresh_from_db()
            run.history[-1]['at']=now.isoformat()
            for log in run.logs: log['at']=now.isoformat()
            Run.objects.filter(pk=run.pk).update(created_at=now,started_at=now,finished_at=now,heartbeat_at=now,history=run.history,logs=run.logs)
        self.stdout.write(self.style.SUCCESS('Created MALL demo project; all reports are labeled SIMULATION.'))
