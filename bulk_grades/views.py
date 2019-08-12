"""
CSV import/export API for grades.
"""
from __future__ import absolute_import, unicode_literals

import datetime
import json
import logging

from django.http import HttpResponse, HttpResponseForbidden, StreamingHttpResponse
from django.views.generic import View

from . import api

log = logging.getLogger(__name__)


class GradeOnlyExportMixin(object):
    """
    CSV Export of grade information only. To be used by both bulk grade export and interventions.
    """

    def _configure(self, request, course_id):
        """
        Dispatch django request.
        """
        if not (request.user.is_staff or request.user.has_perm('bulk_grades', course_id)):
            return HttpResponseForbidden('Not Staff')
        self.operation_id = request.GET.get('error_id', '')
        if self.operation_id:
            self.processor = api.GradeCSVProcessor.load(self.operation_id)
            if self.processor.course_id != course_id:
                return HttpResponseForbidden()
        else:
            assignment_grade_max = request.GET.get('assignmentGradeMax')
            assignment_grade_min = request.GET.get('assignmentGradeMin')
            self.processor = api.GradeCSVProcessor(
                                                  course_id=course_id,
                                                  _user=request.user,
                                                  track=request.GET.get('track'),
                                                  cohort=request.GET.get('cohort'),
                                                  subsection=request.GET.get('assignment'),
                                                  assignment_type=request.GET.get('assignmentType'),
                                                  subsection_grade_max=(float(assignment_grade_max)
                                                                        if assignment_grade_max else None),
                                                  subsection_grade_min=(float(assignment_grade_min)
                                                                        if assignment_grade_min else None),
                                                )

    def _create_iterator_for_export(self, course_id):
        """
        Create an iterator to do for export of grades.
        """
        iterator = self.processor.get_iterator(error_data=bool(self.operation_id))
        self.filename = [course_id]

        if self.operation_id:
            self.filename.append('graded-results')
        self.filename.append(datetime.datetime.utcnow().isoformat())
        return iterator


class GradeImportExport(View, GradeOnlyExportMixin):
    """
    CSV Grade import/export view.
    """

    def dispatch(self, request, course_id, *args, **kwargs):  # pylint: disable=arguments-differ
        """
        Dispatch django request.
        """
        self._configure(request, course_id)
        return super(GradeImportExport, self).dispatch(request, course_id, *args, **kwargs)

    def get(self, request, course_id, *args, **kwargs):  # pylint: disable=unused-argument
        """
        Export grades in CSV format.

        GET arguments:
        track: name of enrollment mode
        cohort: name of cohort
        subsection: block id of graded subsection
        """
        iterator = self._create_iterator_for_export(course_id)
        response = StreamingHttpResponse(iterator, content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="%s.csv"' % '-'.join(self.filename)

        log.info('Exporting grade CSV for %s', course_id)
        return response

    def post(self, request, course_id, *args, **kwargs):  # pylint: disable=unused-argument
        """
        Import grades from a CSV file.
        """
        result_id = request.POST.get('result_id', None)
        if result_id:
            results = self.processor.get_deferred_result(result_id)
            if results.ready():
                data = results.get()
                log.info('Got results from celery %r', data)
            else:
                data = {'waiting': True, 'result_id': result_id}
                log.info('Still waiting for %s', result_id)
        else:
            the_file = request.FILES['csv']
            self.processor.process_file(the_file, autocommit=True)
            data = self.processor.status()
            log.info('Processed file %s for %s -> %s saved, %s processed, %s error. (async=%s)',
                     the_file.name,
                     course_id,
                     data.get('saved', 0),
                     data.get('total', 0),
                     len(data.get('error_rows', [])),
                     data.get('waiting', False))
        return HttpResponse(json.dumps(data), content_type='application/json')


class GradeOperationHistoryView(View):
    """
    Collection View for history of grade override file uploads.
    """

    def get(self, request, course_id):
        """
        Get all previous times grades have been overwritten for this course.
        """
        history = self.processor.get_committed_history()
        return HttpResponse(json.dumps(history), content_type='application/json')

    def dispatch(self, request, course_id, *args, **kwargs):  # pylint: disable=arguments-differ
        """
        General set-up method for all handler messages in this view.
        """
        if not (request.user.is_staff or request.user.has_perm('bulk_grades', course_id)):
            return HttpResponseForbidden('Not Staff')
        self.processor = api.GradeCSVProcessor(  # pylint: disable=attribute-defined-outside-init
            course_id=course_id,
            _user=request.user
        )
        return super(GradeOperationHistoryView, self).dispatch(request, course_id, *args, **kwargs)


class InterventionsExport(View, GradeOnlyExportMixin):
    """
    Interventions export view.
    """

    def dispatch(self, request, course_id, *args, **kwargs):  # pylint: disable=arguments-differ
        """
        Dispatch django request.
        """
        self._configure(request, course_id)
        return super(InterventionsExport, self).dispatch(request, course_id, *args, **kwargs)

    def get(self, request, course_id, *args, **kwargs):  # pylint: disable=unused-argument
        """
        Export intervention data in CSV format.
        """
        iterator = self._create_iterator_for_export(course_id)
        response = StreamingHttpResponse(iterator, content_type='text/csv')
        self.filename.append('intervention')
        response['Content-Disposition'] = 'attachment; filename="%s.csv"' % '-'.join(self.filename)

        log.info('Exporting intervention CSV for %s', course_id)
        return response
