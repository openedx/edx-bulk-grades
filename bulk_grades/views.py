"""
CSV import/export API for grades.
"""
from __future__ import absolute_import, unicode_literals

import json
import logging

from django.views.generic import View

from django.http import HttpResponse, HttpResponseForbidden, StreamingHttpResponse
from . import api

log = logging.getLogger(__name__)


class GradeImportExport(View):
    def dispatch(self, request, course_id, *args, **kwargs):
        if not (request.user.is_staff or user.has_perm('bulk_grades', course_id)):
            return HttpResponseForbidden('Not Staff')
        return super(GradeImportExport, self).dispatch(request, course_id, *args, **kwargs)

    def get(self, request, course_id, *args, **kwargs):
        """
        Export grades in CSV format.
        """
        track = request.GET.get('track', None)
        cohort = request.GET.get('cohort', None)
        processor = api.GradeCSVProcessor(course_id=course_id, track=track, cohort=cohort)
        iterator = processor.get_iterator()
        response = StreamingHttpResponse(iterator, content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="%s.csv"' % course_id

        log.info('Exporting grade CSV for %s', course_id)
        return response

    def post(self, request, course_id, *args, **kwargs):
        """
        Import grades from a CSV file.
        """
        processor = api.GradeCSVProcessor(course_id=course_id)
        result_id = request.POST.get('result_id', None)
        if result_id:
            results = processor.get_deferred_result(result_id)
            if results.ready():
                data = results.get()
                log.info('Got results from celery %r', data)
            else:
                data = {'waiting': True, 'result_id': result_id}
                log.info('Still waiting for %s', result_id)
        else:
            the_file = request.FILES['csv']
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
