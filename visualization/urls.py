from django.urls import path

from . import views

urlpatterns = [
    path('', views.home_view, name='home'),
    path('ops/u001/', views.u001_ops_overview_view, name='u001_ops_overview'),
    path('ops/u001/api/summary/', views.u001_ops_summary_api, name='u001_ops_summary_api'),
    path('ops/u001/automation/', views.u001_ops_automation_view, name='u001_ops_automation'),
    path('ops/u001/api/automation/', views.u001_ops_automation_api, name='u001_ops_automation_api'),
    path('ops/u001/coverage/', views.u001_ops_coverage_view, name='u001_ops_coverage'),
    path('ops/u001/api/coverage/', views.u001_ops_coverage_api, name='u001_ops_coverage_api'),
    path('ops/u001/queues/', views.u001_ops_queues_view, name='u001_ops_queues'),
    path('ops/u001/api/queues/', views.u001_ops_queues_api, name='u001_ops_queues_api'),
    path('ops/u001/coin/<str:mint>/', views.u001_ops_coin_view, name='u001_ops_coin'),
    path('ops/u001/api/coin/<str:mint>/', views.u001_ops_coin_api, name='u001_ops_coin_api'),
    path('ops/u001/trends/', views.u001_ops_trends_view, name='u001_ops_trends'),
    path('ops/u001/api/trends/', views.u001_ops_trends_api, name='u001_ops_trends_api'),
    path('chart/<str:symbol>/', views.chart_view, name='chart'),
    path('api/chart/klines/<str:symbol>/', views.klines_api, name='klines_api'),
    path('api/chart/metrics/<str:symbol>/', views.metrics_api, name='metrics_api'),
    path('api/chart/funding/<str:symbol>/', views.funding_api, name='funding_api'),
]
