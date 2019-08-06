class Location(object):
    def __hash__(self):
        return hash(self.block_id)

    def __eq__(self, other):
        return self.block_id == other.block_id


class Subsection(object):
    def __init__(self, block_id, display_name):
        self.display_name = display_name
        self.location = Location()
        self.location.block_id = block_id


class Grade(object):
    def __init__(self):
        self.earned_all = 1
        self.override = None
        self.possible_all = 1


def prefetch_course_and_subsection_grades(course_id, users):
    pass


def get_subsection_grades(user_id, course_key):
    grades = {}
    for subsection in graded_subsections_for_course_id(course_key):
        grades[subsection.location] = Grade()
    return grades


def override_subsection_grade(user_id, course_id, block_id, **kwargs):
    pass


def graded_subsections_for_course_id(course_id):
    yield Subsection('block-v1:testX+gi101+2019+type@test+block@85bb02dbd2c14ba5bc31a0264b140dda', 'Sub 1')
    yield Subsection('block-v1:testX+gi101+2019+type@test+block@123402dbd2c14ba5bc31a0264b140dda', 'Sub 2')
    yield Subsection('block-v1:testX+gi101+2019+type@test+block@12f402dbd2c14ba5bc31a0264b140dda', 'Sub 3')


class SubsectionGradeFactory(object):
    pass


class CourseGradeFactory(object):
    def read(
            self,
            user,
            course=None,
            collected_block_structure=None,
            course_structure=None,
            course_key=None,
            create_if_needed=True,
    ):
        pass


class task_compute_all_grades_for_course(object):
    @classmethod
    def apply_async(cls, **kwargs):
        pass
