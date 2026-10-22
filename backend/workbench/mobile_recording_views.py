"""Owner-scoped recorder API. Device control is never exposed as an arbitrary MCP proxy."""
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .access import projects_for, require_write
from .mobile_recording import finish_session, queue_action, recover_session, start_session
from .mobile_tools import MobileRecordingError, call_mobile, connected_devices, parse_screen_elements, response_text
from .models import AppRecording, Resource
from .security import audit


def session_data(session):
    recover_session(session)
    return {'id': session.pk, 'project': session.project_id, 'resource': session.resource_id,
            'device_id': session.device_id, 'device_name': session.device_name, 'package_name': session.package_name,
            'status': session.status, 'steps': session.steps, 'error': session.error,
            'width': session.screen_width, 'height': session.screen_height, 'screen_version': session.screen_version,
            'has_screen': bool(session.screenshot), 'job_id': session.last_job_id, 'case_id': session.case_id}


class AppRecordingViewSet(viewsets.ViewSet):
    def project(self, request):
        project = get_object_or_404(projects_for(request.user), pk=request.data.get('project', request.query_params.get('project')))
        require_write(request.user, project)
        return project

    def owned(self, request, pk):
        session = get_object_or_404(AppRecording.objects.select_related('project', 'resource', 'last_job'),
                                   pk=pk, project__in=projects_for(request.user), created_by=request.user)
        require_write(request.user, session.project)
        return recover_session(session)

    def resource(self, request, project):
        return get_object_or_404(Resource, pk=request.data.get('resource'), project=project, kind='mcp', enabled=True, mode='live')

    def list(self, request):
        project = self.project(request)
        sessions = AppRecording.objects.filter(project=project, created_by=request.user).exclude(status__in=['saved', 'cancelled']).order_by('-created_at')
        return Response([session_data(s) for s in sessions[:20]])

    def retrieve(self, request, pk=None):
        return Response(session_data(self.owned(request, pk)))

    def create(self, request):
        project = self.project(request)
        session = start_session(request.user, project, self.resource(request, project), request.data.get('device_id'), request.data.get('package_name'))
        return Response(session_data(session), status=202)

    @action(detail=False, methods=['post'])
    def devices(self, request):
        project = self.project(request)
        try:
            return Response(connected_devices(self.resource(request, project)))
        except MobileRecordingError as exc:
            raise ValidationError(str(exc)) from exc
        except Exception as exc:
            raise ValidationError('无法连接 Mobile MCP；Docker 请使用 host.docker.internal 地址并配置精确白名单，确认宿主机服务和 USB 调试已开启') from exc

    @action(detail=True, methods=['post'], url_path='command')
    def command(self, request, pk=None):
        session = self.owned(request, pk)
        fields = ('action', 'value', 'locator', 'x', 'y', 'screen_version', 'sensitive', 'variable', 'submit')
        command = {key: request.data[key] for key in fields if key in request.data}
        for flag in ('sensitive', 'submit'):
            if flag in command and type(command[flag]) is not bool:
                raise ValidationError('输入选项必须为布尔值')
        return Response(session_data(queue_action(session, command)), status=202)

    @action(detail=True, methods=['get'])
    def screen(self, request, pk=None):
        session = self.owned(request, pk)
        if not session.screenshot:
            raise ValidationError('设备截图尚未就绪')
        response = FileResponse(session.screenshot.open('rb'), content_type='image/png' if session.screenshot.name.endswith('.png') else 'image/jpeg')
        response['Cache-Control'] = 'no-store, private'
        response['X-Content-Type-Options'] = 'nosniff'
        return response

    @action(detail=True, methods=['get'])
    def elements(self, request, pk=None):
        """Return the current accessibility tree for field-based App recording."""
        session = self.owned(request, pk)
        if session.status != 'ready':
            raise ValidationError('请等待设备操作完成后读取页面元素')
        try:
            response = call_mobile(session.resource, 'mobile_list_elements_on_screen',
                                   {'device': session.device_id, 'format': 'text'})
            return Response(parse_screen_elements(response_text(response)))
        except MobileRecordingError as exc:
            raise ValidationError(str(exc)) from exc
        except Exception as exc:
            raise ValidationError('无法读取设备页面元素，请检查 Mobile MCP 连接并刷新画面') from exc

    @action(detail=True, methods=['post'])
    def finish(self, request, pk=None):
        session = self.owned(request, pk)
        case = finish_session(session, request.user, request.data.get('name'))
        return Response({'case_id': case.pk, 'name': case.name})

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        session = self.owned(request, pk)
        if session.status == 'cancelled':
            return Response(status=204)
        if not AppRecording.objects.filter(pk=session.pk, status__in=['ready', 'error']).update(status='cancelled', device_lock=None, pending_encrypted=''):
            raise ValidationError('请等待当前设备操作结束后取消')
        if session.screenshot:
            session.screenshot.delete(save=False)
            AppRecording.objects.filter(pk=session.pk).update(screenshot='')
        audit(request.user, session.project, 'app.recording_cancelled', session.pk)
        return Response(status=204)
