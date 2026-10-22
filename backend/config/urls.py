from django.urls import path, include
from rest_framework.routers import DefaultRouter
from workbench import views, platform_views as platform
from workbench.mobile_recording_views import AppRecordingViewSet
router = DefaultRouter()
router.register('app-recordings', AppRecordingViewSet, basename='app-recordings')
for name, view in [('projects',views.ProjectViewSet),('resources',views.ResourceViewSet),('assets',views.AssetViewSet),('runs',views.RunViewSet),('attachments',views.AttachmentViewSet)]:
    router.register(name,view,basename=name)
router.register('entities',platform.EntityViewSet,basename='entities')
router.register('jobs',platform.JobViewSet,basename='jobs')
router.register('artifacts',platform.ArtifactViewSet,basename='artifacts')
urlpatterns = [path('api/preferences/',platform.preferences),path('api/access-keys/',platform.keys),path('api/system/users/',platform.users),path('api/system/organizations/',platform.organizations),path('api/system/config/',platform.system),path('api/system/audit/',platform.audit_events),path('api/auth/session/',views.session),path('api/auth/login/',views.sign_in),path('api/auth/logout/',views.sign_out),path('api/',include(router.urls))]
