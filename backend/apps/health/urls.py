from django.urls import path
from . import views

urlpatterns = [
    path("", views.health, name="health"),
    path("db/", views.health_db, name="health-db"),
    path("redis/", views.health_redis, name="health-redis"),
    path("mail-engine/", views.health_mail_engine, name="health-mail-engine"),
]
