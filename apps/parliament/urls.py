from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("glosowania/", views.voting_list, name="voting_list"),
    path("glosowania/<int:sitting>/<int:number>/", views.voting_detail, name="voting_detail"),
    path("poslowie/", views.mp_list, name="mp_list"),
    path("poslowie/<int:sejm_id>/", views.mp_detail, name="mp_detail"),
    path("poslowie/<int:sejm_id>/glosy/", views.mp_votes, name="mp_votes"),
    path("kluby/", views.club_list, name="club_list"),
    path("kluby/<str:code>/", views.club_detail, name="club_detail"),
]
