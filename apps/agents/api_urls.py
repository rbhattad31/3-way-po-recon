from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.agents.views import AgentDefinitionViewSet, AgentRunViewSet

app_name = "agents_api"

router = DefaultRouter()
router.register("definitions", AgentDefinitionViewSet, basename="definition")
router.register("runs", AgentRunViewSet, basename="run")

urlpatterns = [
    path("", include(router.urls)),
]
