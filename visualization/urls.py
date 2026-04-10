from django.urls import path

from . import views

urlpatterns = [
    path('', views.home_view, name='home'),
    path('chart/<str:symbol>/', views.chart_view, name='chart'),
    path('api/chart/klines/<str:symbol>/', views.klines_api, name='klines_api'),
    path('api/chart/metrics/<str:symbol>/', views.metrics_api, name='metrics_api'),
    path('api/chart/funding/<str:symbol>/', views.funding_api, name='funding_api'),
]
