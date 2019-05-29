from __future__ import absolute_import, unicode_literals

import logging
import json

from django.views.generic import View

from django.http import HttpResponse, HttpResponseForbidden, StreamingHttpResponse
from . import api

log = logging.getLogger(__name__)

class Echo:
    """An object that implements just the write method of the file-like
    interface.
    """
    def write(self, value):
        """Write the value by returning it, instead of storing in a buffer."""
        return value


class GradeImportExport(View):
    def dispatch(self, request, course_id, *args, **kwargs):
        if not (request.user.is_staff or user.has_perm('bulk_grades', course_id)):
            return HttpResponseForbidden('Not Staff')
        return super(GradeImportExport, self).dispatch(request, course_id, *args, **kwargs)

    def get(self, request, course_id, *args, **kwargs):
        # import pdb;pdb.set_trace()
        track = request.GET.get('track', None)
        cohort = request.GET.get('cohort', None)
        fake_buffer = Echo()
        processor = api.GradeCSVProcessor(course_id=course_id, track=track, cohort=cohort)
        iterator = processor.write_file(fake_buffer, make_iterator=True)
        response = StreamingHttpResponse(iterator, content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="%s.csv"' % course_id

        log.info('Exporting grade CSV for %s', course_id)
        return response

    def post(self, request, course_id, *args, **kwargs):
        the_file = request.FILES['csv']
        processor = api.GradeCSVProcessor(course_id=course_id)
        processor.process_file(the_file, autocommit=True)
        data = processor.status()
        log.info('Processed file %s for %s -> %s saved, %s processed, %s error. (async=%s)',
                 the_file.name,
                 course_id,
                 data.get('saved', 0),
                 data.get('total', 0),
                 len(data.get('error_rows', [])),
                 data.get('waiting', False))
        return HttpResponse(json.dumps(data), content_type='application/json')
