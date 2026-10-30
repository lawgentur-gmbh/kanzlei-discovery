"""Last-resort cloud alert, including failures before a DB connection exists."""
from runtime import notify
notify({'issues':['cloud_workflow_failed_or_timed_out'],'action':'Check the weekly workflow run. Database history and existing jobs were not deleted.'})
