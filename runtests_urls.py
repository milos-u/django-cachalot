from django.urls import re_path, include
from django.http import HttpResponse


def empty_page(request):
    return HttpResponse('<body></body>')


urlpatterns = [
    re_path(r'^$', empty_page),
]

# Debug toolbar je volitelny — viz settings.py. Kdyz chybi, nesmi na nem
# spadnout ani URLconf: ten se vyhodnocuje az pri prvnim pouziti, takze by
# to neshodilo django.setup(), ale az bezici testy.
try:
    import debug_toolbar
except ImportError:
    pass
else:
    urlpatterns += [
        re_path(r'^__debug__/', include(debug_toolbar.urls)),
    ]
