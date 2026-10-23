import json, math, hashlib, re, zipfile, subprocess, tempfile, shutil
from pathlib import Path
from django.db import transaction
from rest_framework import serializers
from .models import Entity, Revision, Resource, Asset
from .serializers import ScopedSerializer

class EntitySerializer(ScopedSerializer):
    class Meta:
        model=Entity
        fields=['id','project','kind','name','content','config','parent','position','version','created_at','updated_at']
        read_only_fields=['version','created_at','updated_at']
    def validate(self,attrs):
        project=attrs.get('project',getattr(self.instance,'project',None))
        config=attrs.get('config',getattr(self.instance,'config',{}))
        if not isinstance(config,dict): raise serializers.ValidationError('config 必须为对象')
        if len(json.dumps(config))>200000: raise serializers.ValidationError('配置过大')
        kind=attrs.get('kind',getattr(self.instance,'kind',None))
        if kind=='data_preparation':
            from .data_preparation import prepare_data
            try:
                config=prepare_data(config)
            except (ValueError, RecursionError) as exc:
                raise serializers.ValidationError({'config':str(exc)})
            attrs['config']=config
        if kind=='requirement':
            if config.get('status','draft') not in ['draft','parsing','ready','reviewing','approved','archived']:
                raise serializers.ValidationError('无效需求状态')
            if config.get('priority','P1') not in ['P0','P1','P2','P3']:
                raise serializers.ValidationError('无效需求优先级')
            for field,limit in [('owner',100),('acceptance_criteria',20000)]:
                if not isinstance(config.get(field,''),str) or len(config.get(field,''))>limit:
                    raise serializers.ValidationError(f'{field} 必须为不超过 {limit} 字符的文本')
            tags=config.get('tags',[])
            if not isinstance(tags,list) or len(tags)>20 or any(not isinstance(tag,str) or not tag.strip() or len(tag)>40 for tag in tags):
                raise serializers.ValidationError('需求标签最多 20 个，每个标签为 1–40 字符')
        if kind=='ui_case':
            config=dict(config)
            config.setdefault('platform','app' if config.get('executor')=='mobile-mcp' else 'web')
            if config['platform'] not in ['app','web']:
                raise serializers.ValidationError('UI 用例类型仅支持 App 或 Web')
            if config['platform']=='web' and config.get('executor')=='mobile-mcp':
                raise serializers.ValidationError('Web 用例不能使用移动端执行器')
            if config['platform']=='app' and config.get('automation_status')!='needs_mapping' and config.get('executor')!='mobile-mcp':
                raise serializers.ValidationError('可执行 App 用例必须使用 Mobile MCP')
            attrs['config']=config
        if kind=='machine':
            if config.get('executor') not in ['local-playwright-chromium','local-android-adb']:
                raise serializers.ValidationError('仅支持本地 Chromium 或 Android ADB 测试机')
            serial=config.get('serial','')
            if not isinstance(serial,str) or (serial and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',serial)):
                raise serializers.ValidationError('设备序列号格式无效')
        if kind in ['api','ui_case'] and not (kind=='ui_case' and config.get('automation_status')=='needs_mapping'):
            from .executors import validate_config
            try:validate_config(kind,config)
            except ValueError as exc:raise serializers.ValidationError(str(exc))
        if kind=='ui_case' and config.get('automation_status')=='needs_mapping':
            if not isinstance(config.get('design_steps'),list) or not config['design_steps'] or config.get('steps'):
                raise serializers.ValidationError('待配置 UI 用例须保留设计步骤，且不能包含可执行步骤')
        if kind=='ui_case' and config.get('executor')=='mobile-mcp' and config.get('automation_status')!='needs_mapping':
            if not Resource.objects.filter(project=project,pk=config['mcp_resource_id'],kind='mcp',enabled=True).exists():
                raise serializers.ValidationError('请选择当前项目已启用的 Mobile MCP 资源')
        if kind=='ui_flow':
            platform=config.get('platform','web');ids=config.get('case_ids',[])
            if platform not in ['app','web']:raise serializers.ValidationError({'config':'测试流程平台仅支持 App 或 Web'})
            if not isinstance(ids,list) or len(ids)>100 or any(type(value) is not int for value in ids) or len(set(ids))!=len(ids):
                raise serializers.ValidationError({'config':'case_ids 须为不重复的整数数组，最多 100 条'})
            if config.get('status','draft') not in ['draft','ready']:raise serializers.ValidationError({'config':'流程状态仅支持草稿或就绪'})
            environment_id=config.get('environment_id')
            if environment_id is not None and (type(environment_id) is not int or not Entity.objects.filter(pk=environment_id,project=project,kind='ui_environment').exists()):
                raise serializers.ValidationError({'config':'流程环境必须引用当前项目 UI 环境'})
            if ids:
                cases=list(Entity.objects.filter(project=project,kind='ui_case',pk__in=ids))
                if len(cases)!=len(ids) or any(case.config.get('platform')!=platform for case in cases):
                    raise serializers.ValidationError({'config':'流程用例须属于当前项目且与流程平台一致'})
                if config.get('status')=='ready' and any(case.config.get('automation_status')=='needs_mapping' for case in cases):
                    raise serializers.ValidationError({'config':'就绪流程不能关联待配置执行步骤的用例'})
            elif config.get('status')=='ready':raise serializers.ValidationError({'config':'就绪流程至少需要一个用例'})
        def check_secrets(value,path='config'):
            if isinstance(value,dict):
                for k,v in value.items():
                    if re.search(r'password|secret|token|authorization|cookie|api.?key|^key$',k,re.I) and isinstance(v,str) and v and '{{secret:' not in v:
                        raise serializers.ValidationError({'config':f'{path}.{k} 包含敏感值，请改用 {{secret:NAME}} 引用服务端环境变量 TESTPILOT_SECRET_NAME，例如 {{secret:JUHE_API_KEY}}'})
                    check_secrets(v,f'{path}.{k}')
            elif isinstance(value,list):
                for index,v in enumerate(value):check_secrets(v,f'{path}[{index}]')
        check_secrets(config)
        content=attrs.get('content','')
        if len(content)>500000: raise serializers.ValidationError('正文最多 500,000 字符')
        parent=attrs.get('parent',getattr(self.instance,'parent',None))
        if kind=='requirement' and parent and parent.kind!='requirement':
            raise serializers.ValidationError('需求父节点必须是需求文档')
        seen=set()
        while parent:
            if parent.project_id!=project.id or (self.instance and parent.pk==self.instance.pk) or parent.pk in seen:
                raise serializers.ValidationError('父节点必须位于同一项目且不能构成循环')
            seen.add(parent.pk);parent=parent.parent
        for field in ['embedding_model','reranker_model','model_id']:
            if config.get(field) and not Resource.objects.filter(project=project,kind='model',pk=config[field]).exists():
                raise serializers.ValidationError(f'{field} 必须引用当前项目模型')
        return attrs
    @transaction.atomic
    def create(self,attrs):
        entity=super().create(attrs);save_revision(entity,self.context['request'].user);return entity
    @transaction.atomic
    def update(self,instance,attrs):
        instance=Entity.objects.select_for_update().get(pk=instance.pk)
        expected=self.initial_data.get('version')
        if expected is not None and expected!=instance.version: raise serializers.ValidationError('内容已被其他用户修改，请刷新后重试')
        attrs['version']=instance.version+1
        entity=super().update(instance,attrs);save_revision(entity,self.context['request'].user);return entity

def save_revision(entity,user):
    Revision.objects.create(entity=entity,version=entity.version,name=entity.name,content=entity.content,config=entity.config,created_by=user)

def parse_document(file):
    suffix=Path(file.name).suffix.lower()
    if suffix=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(file)
        if len(reader.pages)>300: raise ValueError('PDF 最多 300 页')
        text='\n'.join(p.extract_text() or '' for p in reader.pages)
    elif suffix=='.docx':
        with zipfile.ZipFile(file) as z:
            if sum(i.file_size for i in z.infolist())>30*1024*1024: raise ValueError('DOCX 解压后超过 30MB')
        file.seek(0)
        from docx import Document
        document=Document(file)
        text='\n'.join([p.text for p in document.paragraphs]+[' | '.join(c.text for c in row.cells) for t in document.tables for row in t.rows])
    elif suffix=='.doc':
        # Keep converter selection explicit: textutil serves macOS, antiword serves Linux containers.
        # Uploaded filenames never enter the command line.
        with tempfile.TemporaryDirectory(prefix='testpilot-doc-') as directory:
            source=Path(directory)/'source.doc';target=Path(directory)/'source.txt'
            source.write_bytes(file.read())
            textutil=shutil.which('textutil') or ('/usr/bin/textutil' if Path('/usr/bin/textutil').is_file() else None)
            antiword=shutil.which('antiword')
            if not textutil and not antiword:
                raise ValueError('DOC 解析工具不可用，请转换为 DOCX 或 Markdown 后重试')
            try:
                if textutil:
                    result=subprocess.run([textutil,'-convert','txt','-encoding','UTF-8','-output',str(target),str(source)],
                                          capture_output=True,timeout=20,check=False)
                    if result.returncode or not target.exists():
                        raise ValueError('DOC 文件解析失败，请确认文件未损坏或转换为 DOCX 后重试')
                    text=target.read_text(encoding='utf-8',errors='replace')
                else:
                    result=subprocess.run([antiword,str(source)],capture_output=True,timeout=20,check=False)
                    if result.returncode:
                        raise ValueError('DOC 文件解析失败，请确认文件未损坏或转换为 DOCX 后重试')
                    text=result.stdout.decode('utf-8',errors='replace')
            except (OSError,subprocess.TimeoutExpired) as exc:
                raise ValueError('DOC 解析工具不可用，请转换为 DOCX 或 Markdown 后重试') from exc
    elif suffix=='.xlsx':
        try:
            with zipfile.ZipFile(file) as archive:
                if sum(item.file_size for item in archive.infolist())>30*1024*1024:
                    raise ValueError('Excel 文件解压后超过 30MB')
            file.seek(0)
            from openpyxl import load_workbook
            from openpyxl.utils.exceptions import InvalidFileException
            workbook=load_workbook(file,read_only=True,data_only=True)
        except (OSError,zipfile.BadZipFile,ValueError,InvalidFileException) as exc:
            if isinstance(exc,ValueError) and str(exc)=='Excel 文件解压后超过 30MB':raise
            raise ValueError('Excel 文件无法解析，请确认文件未损坏') from exc
        try:
            lines=[];cell_count=0
            for sheet in workbook.worksheets[:20]:
                lines.append(f'## 工作表：{sheet.title}')
                headers=[]
                for row_number,row in enumerate(sheet.iter_rows(values_only=True),start=1):
                    if row_number>200 or cell_count>=20000:break
                    values=[]
                    for value in row[:50]:
                        cell_count+=1
                        if value is None:values.append('');continue
                        value=value.isoformat() if hasattr(value,'isoformat') else str(value)
                        values.append(value[:1000])
                    if not any(values):continue
                    if row_number==1:
                        headers=[value.strip() or f'列{index}' for index,value in enumerate(values,start=1)]
                        lines.append('列名：'+' | '.join(headers))
                    else:
                        cells=[f'{headers[index] if index<len(headers) else f"列{index+1}"}：{value}'
                               for index,value in enumerate(values) if value.strip()]
                        if cells:lines.append('- '+'；'.join(cells))
                    if cell_count>=20000:break
                if cell_count>=20000:break
            text='\n'.join(lines)
        finally:
            workbook.close()
    elif suffix in ['.txt','.md','.json','.csv']:
        text=file.read().decode('utf-8-sig')
    else: raise ValueError('支持 UTF-8 TXT、MD、JSON、CSV、DOCX 与有文本层的 PDF')
    if not text.strip(): raise ValueError('未解析到文字，扫描版 PDF 请先 OCR')
    if len(text)>500000: raise ValueError('文档正文超过 500,000 字符')
    return text

def hash_vector(text):
    tokens=re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]',text.lower())
    tokens += [tokens[i]+tokens[i+1] for i in range(len(tokens)-1)]
    vector=[0.0]*256
    for token in tokens:
        digest=hashlib.sha256(token.encode()).digest();vector[int.from_bytes(digest[:2],'big')%256]+=1
    length=math.sqrt(sum(x*x for x in vector)) or 1
    return [x/length for x in vector]
