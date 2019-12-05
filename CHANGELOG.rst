Change Log
----------

..
   All enhancements and patches to bulk_grades will be documented
   in this file.  It adheres to the structure of http://keepachangelog.com/ ,
   but in reStructuredText instead of Markdown (for ease of incorporation into
   Sphinx documentation and the PyPI description).

   This project adheres to Semantic Versioning (http://semver.org/).

.. There should always be an "Unreleased" section for changes pending release.

Unreleased
~~~~~~~~~~
*

[0.6.5] - 2019-12-05
~~~~~~~~~~~~~~~~~~~~~
* In ``get_scores()``, account for case where no ``ScoreOverrider`` exists.

[0.6.4] - 2019-09-24
~~~~~~~~~~~~~~~~~~~~~
* ``GradeCSVProcessor.save()`` should return something.

[0.6.3] - 2019-09-24
~~~~~~~~~~~~~~~~~~~~~
* Upgrade super-csv to 0.9.4, make sure to pass ``user_id`` to GradeCSVProcessor.__init__().

[0.6.2] - 2019-09-23
~~~~~~~~~~~~~~~~~~~~~
* Upgrade super-csv to 0.9.3

[0.6.1] - 2019-09-17
~~~~~~~~~~~~~~~~~~~~~
* Call grades api with `comment` when doing bulk upload
* Add `user_id` field to GradeCSVProcessor to fix bulk_upload history entries

[0.6.0] - 2019-09-10
~~~~~~~~~~~~~~~~~~~~~
* Prevent Grade and Intervention CSV processors from producing duplicate columns.

[0.5.10] - 2019-09-06
~~~~~~~~~~~~~~~~~~~~~
* Prevent user from setting negative grades

[0.5.9] - 2019-08-28
~~~~~~~~~~~~~~~~~~~~
* Make intervention report display either grade override if exists or original grade.

[0.5.8] - 2019-08-26
~~~~~~~~~~~~~~~~~~~~
* Make intervention masters track nly. Some clan up.

[0.5.3] - 2019-08-16
~~~~~~~~~~~~~~~~~~~~
* Add support for filters to Interventions CSV report endpoint, mirroring bulk management filters

[0.5.2] - 2019-08-15
~~~~~~~~~~~~~~~~~~~~
* Bring datasource for grade information inline with what the rest of gradebook uses

[0.4.4] - 2019-08-13
~~~~~~~~~~~~~~~~~~~~
Add ability to filter by course grade, provided as a percentage to the endpoint.

[0.4.3] - 2019-08-12
~~~~~~~~~~~~~~~~~~~~
Add ability to filter by subsection grade, provided as a percentage to the endpoint

[0.4.1] - 2019-08-01
~~~~~~~~~~~~~~~~~~~~
Added ability to filter by subsection & assignment grading type for bulk management CSV downloads.

[0.1.4] - 2019-07-02
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Added an endpoint for this history of bulk management operations on grade overrides.

[0.1.0] - 2019-05-24
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Added
_____

* First release on PyPI.
