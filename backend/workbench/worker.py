"""Durable SQLite queue. Atomic claims; recover stale work without deleting history."""
import time, uuid
from datetime import timedelta
from django.utils import timezone
from .models import Run

def recover_stale():
    cutoff = timezone.now() - timedelta(seconds=60)
    for run in Run.objects.filter(status='running',heartbeat_at__lt=cutoff):
        Run.objects.filter(pk=run.pk,status='running',heartbeat_at=run.heartbeat_at).update(status='queued',worker_id='',history=run.history+[{'status':'queued','at':timezone.now().isoformat(),'message':'Worker 中断，任务已恢复入队'}])

def claim():
    worker_id = uuid.uuid4().hex
    for pk in Run.objects.filter(status='queued').order_by('created_at').values_list('pk',flat=True)[:10]:
        now = timezone.now()
        if Run.objects.filter(pk=pk,status='queued').update(status='running',worker_id=worker_id,heartbeat_at=now,started_at=now):
            run = Run.objects.get(pk=pk)
            run.history += [{'status':'running','at':now.isoformat(),'message':'模拟执行器开始处理'}]
            Run.objects.filter(pk=pk,worker_id=worker_id,status='running').update(history=run.history)
            return run
    return None

def execute(run, delay=0.4):
    start = time.monotonic()
    results, logs = [], [{'at':timezone.now().isoformat(),'level':'INFO','message':'[SIMULATION] 此任务未连接真实模型、MCP 或设备；结果由用例中的模拟结果配置生成。'}]
    try:
        for index, case in enumerate(run.snapshot):
            if not Run.objects.filter(pk=run.pk,status='running',worker_id=run.worker_id).exists(): return
            outcome = case['data'].get('simulate_result','passed')
            steps = []
            for number, step in enumerate(case['data'].get('steps',[])):
                # Deterministic simulation, never sends network requests or executes user commands.
                steps.append({'index':number,'action':step['action'],'expected':step['expected'],'actual':'模拟：'+('预设断言未通过' if outcome == 'failed' and number == len(case['data']['steps'])-1 else '步骤结果由模拟器生成'),'status':outcome if number == len(case['data']['steps'])-1 or outcome == 'skipped' else 'passed','duration_ms':0,'screenshots':[]})
            time.sleep(delay)
            results.append({'case_id':case['id'],'name':case['name'],'priority':case['priority'],'status':outcome,'steps':steps,'duration_ms':round(delay*1000)})
            logs.append({'at':timezone.now().isoformat(),'level':'WARN' if outcome == 'failed' else 'INFO','message':f'[SIMULATION] 用例 #{case["id"]}：{outcome}'})
            if not Run.objects.filter(pk=run.pk,status='running',worker_id=run.worker_id).update(results=results,logs=logs,progress=round((index+1)/len(run.snapshot)*95),heartbeat_at=timezone.now()): return
        metrics = {'total':len(results),'passed':sum(r['status']=='passed' for r in results),'failed':sum(r['status']=='failed' for r in results),'skipped':sum(r['status']=='skipped' for r in results),'duration_ms':round((time.monotonic()-start)*1000)}
        metrics['pass_rate'] = round(metrics['passed']/metrics['total']*100,1) if metrics['total'] else 0
        now = timezone.now()
        history = run.history+[{'status':'completed','at':now.isoformat(),'message':'模拟执行完成，报告已持久化'}]
        Run.objects.filter(pk=run.pk,status='running',worker_id=run.worker_id).update(status='completed',progress=100,metrics=metrics,results=results,logs=logs,history=history,finished_at=now,heartbeat_at=now)
    except Exception:
        now = timezone.now()
        # Avoid logging arbitrary exception strings which may contain credentials.
        Run.objects.filter(pk=run.pk,status='running',worker_id=run.worker_id).update(status='failed',finished_at=now,logs=logs+[{'at':now.isoformat(),'level':'ERROR','message':'执行器内部异常，请检查服务端配置'}],history=run.history+[{'status':'failed','at':now.isoformat(),'message':'任务失败'}])
