from django.urls import path

from . import views

urlpatterns = [
    path("callback/", views.callback, name="document-node-callback"),
    path("feedback/", views.feedback, name="document-node-feedback"),
]
